"""TaskService — ядро бизнес-логики Дирижёра.

Полностью независим от Telegram/UI, поэтому легко тестируется. Реализует
конвейер: извлечение (LLM) → нормализация исполнителя → классификация
(новая / дубль / низкая уверенность) → создание/правка карточки + память.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from ..config import Settings
from ..domain.enums import TaskStatus
from ..domain.models import SourceRef, Task
from ..integrations.yougile import BoardClient
from ..llm.base import ExtractionContext, LLMProvider
from ..logging_setup import get_logger
from ..memory.project_snapshot import ProjectSnapshot
from ..memory.vector_store import TaskMemory
from ..repository import TaskRepository, TeamRegistry

log = get_logger("dirizher.service")

import re as _re

_ASSIGNEE_SPLIT = _re.compile(r"\s*(?:,|;|/|&|\bи\b|\band\b)\s*", _re.IGNORECASE)
_MENTION_RE = _re.compile(r"@([A-Za-z0-9_]{3,})")
_WORD_RE = _re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_]*")
# Маркеры режима «добавить к исполнителям», а не «заменить»
_APPEND_KW = ("добав", "ещё", "еще", "к исполнит", "плюс", "также", "и ещё")


# «Пустые» заголовки — это команда-фиксатор, попавшая в title по ошибке LLM.
_JUNK_TITLE_RE = _re.compile(
    r"^\W*(?:"
    r"созда(?:ть|й|ем)\s+задач\w*|поставь?\s+задач\w*|поставить\s+задач\w*|"
    r"заведи\s+задач\w*|запиши\s+задач\w*|оформи\s+задач\w*|зафиксир\w*|"
    r"таск\w*|задач\w*|на\s+доску|сделать\s+задач\w*"
    r")\W*$",
    _re.IGNORECASE,
)


def _is_junk_title(title: str) -> bool:
    return bool(_JUNK_TITLE_RE.match(title.strip()))


def _split_assignees(name: str | None) -> list[str]:
    """«Данила и Андрей» → ['Данила', 'Андрей']. Один @username не дробим."""
    if not name or not name.strip():
        return []
    if name.startswith("@") and " " not in name:
        return [name]
    parts = [p.strip() for p in _ASSIGNEE_SPLIT.split(name) if p.strip()]
    return parts or [name.strip()]


class Outcome(str, Enum):
    new = "new"                      # новая задача -> на подтверждение/создание
    duplicate = "duplicate"          # совпадает с существующей -> объединение
    low_confidence = "low_confidence"  # ниже порога -> уточнить у участника


@dataclass
class ProcessedTask:
    task: Task
    outcome: Outcome
    duplicate_of: Task | None = None
    dup_score: float | None = None


class TaskService:
    def __init__(
        self,
        settings: Settings,
        provider: LLMProvider,
        board: BoardClient,
        memory: TaskMemory,
        snapshot: ProjectSnapshot,
        repo: TaskRepository,
        team: TeamRegistry,
        persist: "Callable[[], None] | None" = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.board = board
        self.memory = memory
        self.snapshot = snapshot
        self.repo = repo
        self.team = team
        self._persist = persist or (lambda: None)
        # Резервный извлекатель на случай недоступности LLM (лимит/сеть).
        from ..llm.mock_provider import MockLLMProvider
        self._fallback = MockLLMProvider()
        self._llm_degraded = False  # True, если последний вызов LLM упал

    # ── Извлечение и классификация ───────────────────────────────────────────
    async def ingest(
        self,
        text: str,
        source: SourceRef,
        *,
        today: date | None = None,
        history: list[str] | None = None,
    ) -> list[ProcessedTask]:
        ctx = ExtractionContext(
            today=today or date.today(),
            source_label=source.source.label_ru,
            team=self.team.all(),
            memory_context=self.snapshot.context_for_llm(self.repo.all()),
            recent_dialog=history or [],
        )
        try:
            extracted = await self.provider.extract_tasks(text, ctx)
        except Exception as e:  # noqa: BLE001  — лимит/сеть LLM не должны ронять бота
            log.warning("LLM недоступен (%s) — откат на эвристику", type(e).__name__)
            self._llm_degraded = True
            extracted = await self._fallback.extract_tasks(text, ctx)
        else:
            self._llm_degraded = False
        log.info("Извлечено задач: %d (источник=%s)", len(extracted), source.source.value)

        thr = self.settings.llm.confidence_threshold
        ignore = self.settings.llm.ignore_threshold

        # Источник для проверки «упомянут ли исполнитель» (анти-галлюцинация фан-аута).
        haystack = f"{text} {' '.join(history or [])}".lower()

        processed: list[ProcessedTask] = []
        for ex in extracted:
            # Порог тишины: совсем неуверенные — молча игнорируем (анти-спам).
            if ex.confidence < ignore:
                log.info("Пропуск (conf=%.2f < %.2f): %s", ex.confidence, ignore, ex.task)
                continue
            # Заголовок-«команда» без сути («создать задачу») — не заводим карточку.
            if _is_junk_title(ex.task):
                log.info("Пропуск пустого заголовка-команды: %s", ex.task)
                continue
            low_conf = ex.confidence < thr

            # Мульти-исполнители: «Данила и Андрей» → отдельная карточка каждому.
            assignees = _split_assignees(ex.assignee) or [None]
            for who in assignees:
                task = Task.from_extracted(ex, source)
                task.assignee = who
                member = self.team.resolve(task.assignee)
                if member:
                    task.assignee = member.username or member.full_name or task.assignee

                # Анти-галлюцинация: если LLM навесил задачу на человека, которого в
                # тексте/переписке нет, — это выдуманное «навешивание на всю команду»
                # (фан-аут). Такую задачу не заводим.
                if who and not self._assignee_grounded(who, member, haystack):
                    log.info("Пропуск: исполнитель «%s» не упомянут в источнике — выдумка", who)
                    continue

                if low_conf:
                    processed.append(ProcessedTask(task, Outcome.low_confidence))
                    continue

                dup = self.memory.find_duplicate(task.dedup_text())
                existing = self.repo.get(dup.task_id) if dup else None
                # дубль засчитываем только при совпадении исполнителя
                if existing and (existing.assignee or "").lower() == (task.assignee or "").lower():
                    processed.append(
                        ProcessedTask(task, Outcome.duplicate, duplicate_of=existing, dup_score=dup.score)
                    )
                else:
                    processed.append(ProcessedTask(task, Outcome.new))
        return self._dedup_batch(processed)

    def _assignee_grounded(self, who: str, member, haystack: str) -> bool:
        """Упомянут ли исполнитель в тексте/переписке (а не выдуман LLM)."""
        cands: list[str] = []
        if member:
            cands += [member.username or "", member.full_name or "", *member.aliases]
            if member.full_name:
                cands.append(member.full_name.split()[0])
        else:
            cands.append(str(who).lstrip("@"))
        return any(c and c.lower() in haystack for c in cands)

    @staticmethod
    def _dedup_batch(processed: list[ProcessedTask]) -> list[ProcessedTask]:
        """Схлопнуть повторы внутри одной пачки извлечения (одинаковая задача
        на одного исполнителя). Защищает от фан-аута одной фразы в N карточек."""
        seen: set[tuple[str, str]] = set()
        out: list[ProcessedTask] = []
        for p in processed:
            key = (p.task.title.strip().lower(), (p.task.assignee or "").strip().lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        return out

    # ── Применение решений ───────────────────────────────────────────────────
    def _people_in(self, text: str) -> list[tuple[str, str | None]]:
        """Найти исполнителей в тексте правки → [(отображаемое_имя, yougile_id|None)].

        Учитываем @упоминания и имена/алиасы известных участников, встретившиеся
        как отдельные слова. Порядок сохраняем, дубликаты убираем.
        """
        found: list[tuple[str, str | None]] = []
        seen: set[str] = set()

        def _add(disp: str, yid: str | None) -> None:
            key = disp.lstrip("@").lower()
            if key and key not in seen:
                seen.add(key)
                found.append((disp, yid))

        for m in _MENTION_RE.finditer(text):
            handle = m.group(1)
            member = self.team.resolve(handle)
            if member:
                _add(member.username or member.full_name, member.yougile_id)
            else:
                _add(handle, None)

        tokens = {w.lower() for w in _WORD_RE.findall(text)}
        for member in self.team.all():
            cands = [member.username or "", member.full_name, *member.aliases]
            if member.full_name:
                cands.append(member.full_name.split()[0])
            if any(c and c.lower() in tokens for c in cands):
                _add(member.username or member.full_name, member.yougile_id)
        return found

    def _resolve_yougile_ids(self, assignee: str | None) -> list[str]:
        """Из строки исполнителей («@a, @b») собрать id пользователей YouGile."""
        ids: list[str] = []
        for name in _split_assignees(assignee):
            member = self.team.resolve(name)
            if member and member.yougile_id and member.yougile_id not in ids:
                ids.append(member.yougile_id)
        return ids

    async def create_on_board(self, task: Task) -> Task:
        """Создать карточку на доске и зафиксировать задачу в памяти."""
        task.status = TaskStatus.todo
        # назначить карточку на реальных пользователей YouGile, если они привязаны
        task.assignee_yougile_ids = self._resolve_yougile_ids(task.assignee)
        card_id = await self.board.create_card(task)
        task.board_card_id = card_id
        task.touch()
        self.repo.add(task)
        self.memory.remember(task.id, task.dedup_text())
        self.snapshot.add_decision(f"Создана задача «{task.title}» → {task.assignee or '—'}")
        self._save_snapshot()
        return task

    async def merge_duplicate(self, existing: Task, new_source: SourceRef) -> Task:
        """Объединить источники: задача из чата и со встречи — одна карточка."""
        existing.sources.append(new_source)
        existing.touch()
        self.snapshot.add_decision(
            f"Объединён источник ({new_source.source.label_ru}) для «{existing.title}»"
        )
        self._save_snapshot()
        return existing

    async def edit_task(self, task: Task) -> Task:
        """Применить правки. Если карточка уже на доске — синхронизировать."""
        task.touch()
        if task.board_card_id:
            await self.board.update_card(task.board_card_id, task)
            self.memory.remember(task.id, task.dedup_text())
            self._save_snapshot()
        return task

    async def apply_correction(self, task: Task, correction: str, *, today: date | None = None) -> Task:
        """Применить уточнение пользователя к задаче «в полёте» (до отправки).

        Меняем ТОЛЬКО то, что попросили: срок/время/приоритет/исполнителя.
        Заголовок НЕ перезаписываем (иначе «повысь приоритет» затирало название),
        кроме явного переименования: «назови…», «переименуй…», «название: …».
        """
        from ..llm import mock_provider as mp  # переиспользуем эвристики

        today = today or date.today()
        low = correction.lower()
        changed = False

        dl = mp._parse_deadline(correction, today)
        if dl:
            task.deadline = dl
            changed = True
        tm = mp._parse_time(correction)
        if tm:
            task.deadline_time = tm
            changed = True

        # Приоритет: понижение проверяем первым, чтобы «не срочно» не попало в повышение.
        lower_kw = ("не срочн", "понизь", "пониже", "снизь", "пониз", "ниже", "меньше", "неважн")
        raise_kw = ("повыси", "подними", "поднять", "приоритетн", "важнее", "выше", "срочн", "asap", "горит")
        if any(k in low for k in lower_kw):
            task.priority = task.priority.__class__.low
            changed = True
        elif any(k in low for k in raise_kw):
            task.priority = task.priority.__class__.high
            changed = True

        # Исполнители: поддержка нескольких людей и режима «добавить к».
        people = self._people_in(correction)
        if not people and any(k in low for k in ("назнач", "исполнит", "поставь на", "повесь на")):
            # запасной разбор одиночного (в т.ч. незнакомого) имени
            ctx = ExtractionContext(today=today, team=self.team.all())
            raw, _ = mp._match_assignee(correction, ctx)
            if raw:
                member = self.team.resolve(raw)
                disp = (member.username or member.full_name) if member else raw.lstrip("@")
                people = [(disp, member.yougile_id if member else None)]
        if people:
            append = any(k in low for k in _APPEND_KW)
            names = _split_assignees(task.assignee) if append else []
            ids = list(task.assignee_yougile_ids) if append else []
            for disp, yid in people:
                if disp not in names:
                    names.append(disp)
                if yid and yid not in ids:
                    ids.append(yid)
            task.assignee = ", ".join(names)
            task.assignee_yougile_ids = ids
            changed = True

        # Явное переименование — только по чёткому маркеру.
        m = _re.search(
            r"(?:назови|переименуй|название[:\s]|заголовок[:\s]|перепиши(?:\s+задачу)?\s+как)[:\s]*(.+)",
            correction, _re.IGNORECASE,
        )
        if m and m.group(1).strip():
            new_title = m.group(1).strip(" .,:;«»\"'")
            task.title = new_title[:1].upper() + new_title[1:]
            changed = True

        if not changed:
            await self._note_unrecognized(task, correction)

        task.confidence = max(task.confidence, 0.9)  # пользователь подтвердил вручную
        task.touch()
        return task

    async def _note_unrecognized(self, task: Task, correction: str) -> None:
        """Правку не распознали как изменение поля — добавим как уточнение деталей."""
        extra = correction.strip()
        task.requirements = f"{task.requirements}; {extra}" if task.requirements else extra

    async def set_status(self, task: Task, status: TaskStatus) -> Task:
        task.status = status
        task.touch()
        if task.board_card_id:
            if status == TaskStatus.done:
                await self.board.complete_card(task.board_card_id)
            else:
                await self.board.move_card(task.board_card_id, status)
        self._save_snapshot()
        return task

    async def reconcile_with_board(self) -> int:
        """Синхронизировать память с доской: выкинуть «призраков» — задачи, чьи
        карточки на доске уже удалены (вручную на доске и т.п.).

        Без этого память (state.json) расходится с доской и бот считает лишние
        задачи (ложные «12 открытых», переоценка нагрузки). Возвращает число
        удалённых призраков. Безопасно: на mock-доске и при сбое API ничего не трёт."""
        if getattr(self.board, "name", "") == "mock":
            return 0
        try:
            cards = await self.board.list_cards()
        except Exception as e:  # noqa: BLE001
            log.warning("Сверка с доской пропущена (доска недоступна): %s", e)
            return 0

        live_ids = {c.id for c in cards}
        synced = [t for t in self.repo.all() if t.board_card_id]
        # Предохранитель: если доска вернула пусто, а в памяти есть синхронизированные
        # задачи — это похоже на сбой API, а не на пустую доску. Не удаляем ничего.
        if synced and not live_ids:
            log.warning("Сверка с доской: доска вернула 0 карточек — пропускаю (вероятно сбой).")
            return 0

        removed = 0
        for t in synced:
            if t.board_card_id not in live_ids:
                self.repo.remove(t.id)
                self.memory.forget(t.id)
                removed += 1
        if removed:
            log.info("Сверка с доской: удалено призраков из памяти: %d", removed)
            self._save_snapshot()
        return removed

    async def delete_task(self, task: Task) -> Task:
        """Удалить задачу с доски и из памяти."""
        if task.board_card_id:
            try:
                await self.board.delete_card(task.board_card_id)
            except Exception as e:  # noqa: BLE001
                log.warning("Не удалось удалить карточку %s: %s", task.board_card_id, e)
        self.repo.remove(task.id)
        self.memory.forget(task.id)
        self.snapshot.add_decision(f"Удалена задача «{task.title}»")
        self._save_snapshot()
        return task

    # ── Контроль нагрузки ────────────────────────────────────────────────────
    def workload_warning(self, assignee: str | None, *, max_open: int = 5, max_same_day: int = 3) -> str | None:
        """Предупреждение о перегрузке исполнителя (умная нагрузка из отчёта)."""
        if not assignee:
            return None
        tasks = self.repo.open_by_assignee(assignee)
        if not tasks:
            return None
        from collections import Counter

        by_day = Counter(t.deadline for t in tasks if t.deadline)
        peak_day, peak_n = (by_day.most_common(1)[0] if by_day else (None, 0))
        if len(tasks) >= max_open or peak_n >= max_same_day:
            who = self.team.mention_for(assignee)
            parts = [f"⚠️ Внимание: у {who} {len(tasks)} открытых задач"]
            if peak_n >= max_same_day and peak_day:
                parts.append(f", {peak_n} дедлайнов на {peak_day.isoformat()}")
            parts.append(". Возможно, стоит перераспределить.")
            return "".join(parts)
        return None

    def _save_snapshot(self) -> None:
        try:
            self.snapshot.save(self.team.all(), self.repo.all())
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось сохранить снимок проекта: %s", e)
        # персистентность состояния (команда + задачи) — переживает перезапуск
        self._persist()
