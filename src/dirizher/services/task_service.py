"""TaskService — ядро бизнес-логики Дирижёра.

Полностью независим от Telegram/UI, поэтому легко тестируется. Реализует
конвейер: извлечение (LLM) → нормализация исполнителя → классификация
(новая / дубль / низкая уверенность) → создание/правка карточки + память.
"""

from __future__ import annotations

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
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.board = board
        self.memory = memory
        self.snapshot = snapshot
        self.repo = repo
        self.team = team

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
        extracted = await self.provider.extract_tasks(text, ctx)
        log.info("Извлечено задач: %d (источник=%s)", len(extracted), source.source.value)

        thr = self.settings.llm.confidence_threshold
        ignore = self.settings.llm.ignore_threshold

        processed: list[ProcessedTask] = []
        for ex in extracted:
            # Порог тишины: совсем неуверенные — молча игнорируем (анти-спам).
            if ex.confidence < ignore:
                log.info("Пропуск (conf=%.2f < %.2f): %s", ex.confidence, ignore, ex.task)
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
        return processed

    # ── Применение решений ───────────────────────────────────────────────────
    async def create_on_board(self, task: Task) -> Task:
        """Создать карточку на доске и зафиксировать задачу в памяти."""
        task.status = TaskStatus.todo
        # назначить карточку на реального пользователя YouGile, если он привязан
        member = self.team.resolve(task.assignee)
        if member and member.yougile_id:
            task.assignee_yougile_id = member.yougile_id
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

        ctx = ExtractionContext(today=today, team=self.team.all())
        assignee, _ = mp._match_assignee(correction, ctx)
        if assignee:
            member = self.team.resolve(assignee)
            task.assignee = (member.username or member.full_name) if member else assignee.lstrip("@")
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
                parts.append(f", {peak_n} дедлайна на {peak_day.isoformat()}")
            parts.append(". Возможно, стоит перераспределить.")
            return "".join(parts)
        return None

    def _save_snapshot(self) -> None:
        try:
            self.snapshot.save(self.team.all(), self.repo.all())
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось сохранить снимок проекта: %s", e)
