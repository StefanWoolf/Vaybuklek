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

        processed: list[ProcessedTask] = []
        for ex in extracted:
            task = Task.from_extracted(ex, source)
            # нормализуем каждого исполнителя к каноническому имени/username команды
            normalized: list[str] = []
            for raw in task.assignees:
                member = self.team.resolve(raw)
                name = (member.username or member.full_name) if member else raw
                if name and not any(name.lower() == n.lower() for n in normalized):
                    normalized.append(name)
            task.assignees = normalized

            if ex.confidence < self.settings.llm.confidence_threshold:
                processed.append(ProcessedTask(task, Outcome.low_confidence))
                continue

            dup = self.memory.find_duplicate(task.dedup_text())
            existing = self.repo.get(dup.task_id) if dup else None
            if existing:
                processed.append(
                    ProcessedTask(task, Outcome.duplicate, duplicate_of=existing, dup_score=dup.score)
                )
            else:
                processed.append(ProcessedTask(task, Outcome.new))
        return processed

    # ── Привязка исполнителей к пользователям доски (YouGile) ─────────────────
    def _board_assignee_ids(self, task: Task) -> list[str]:
        """Сопоставить имена/usernames исполнителей с id пользователей доски."""
        ids: list[str] = []
        for name in task.assignees:
            member = self.team.resolve(name)
            if member and member.yougile_id and member.yougile_id not in ids:
                ids.append(member.yougile_id)
        return ids

    # ── Применение решений ───────────────────────────────────────────────────
    async def create_on_board(self, task: Task) -> Task:
        """Создать карточку на доске и зафиксировать задачу в памяти."""
        task.status = TaskStatus.todo
        task.board_assignee_ids = self._board_assignee_ids(task)
        card_id = await self.board.create_card(task)
        task.board_card_id = card_id
        task.touch()
        self.repo.add(task)
        self.memory.remember(task.id, task.dedup_text())
        self.snapshot.add_decision(f"Создана задача «{task.title}» → {task.assignees_display()}")
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
        task.board_assignee_ids = self._board_assignee_ids(task)
        if task.board_card_id:
            await self.board.update_card(task.board_card_id, task)
            self.memory.remember(task.id, task.dedup_text())
            self._save_snapshot()
        return task

    async def delete_task(self, task: Task) -> Task:
        """Удалить задачу с доски, из памяти и из репозитория."""
        if task.board_card_id:
            try:
                await self.board.delete_card(task.board_card_id)
            except Exception as e:  # noqa: BLE001
                log.warning("Не удалось удалить карточку %s на доске: %s", task.board_card_id, e)
        self.memory.forget(task.id)
        self.repo.remove(task.id)
        self.snapshot.add_decision(f"Удалена задача «{task.title}»")
        self._save_snapshot()
        return task

    async def apply_correction(self, task: Task, correction: str, *, today: date | None = None) -> Task:
        """Применить уточнение пользователя к задаче «в полёте» (до отправки).

        Гибрид: детерминированно вытаскиваем срок/приоритет/исполнителя из текста
        правки, плюс пробуем LLM-извлечение для новой формулировки заголовка.
        """
        from ..llm import mock_provider as mp  # переиспользуем эвристики

        today = today or date.today()
        changed = False

        # 1) Явное переименование («переименуй в …», «название: …») — приоритетно,
        #    чтобы новый заголовок не съели другие эвристики.
        new_title = mp.detect_rename(correction)
        if new_title:
            task.title = new_title
            changed = True

        # 2) Срок
        dl = mp._parse_deadline(correction, today)
        if dl:
            task.deadline = dl
            changed = True

        # 3) Приоритет (распознаёт императивы: «повысь», «понизь», «сделай срочной»).
        prio = mp.detect_priority_change(correction)
        if prio is not None:
            task.priority = prio
            changed = True

        # 4) Исполнители (с учётом склонений: «назначь на Дашу» → Даша).
        #    По умолчанию правка заменяет список; слова «добавь», «ещё», «также»,
        #    «подключи», «в помощь» — добавляют к текущим (мульти-исполнители).
        ctx = ExtractionContext(today=today, team=self.team.all())
        mentions = mp.find_team_mentions(correction, ctx)  # [(каноническое, словоформа)]
        surfaces: list[str] = [surf for _, surf in mentions]
        names: list[str] = []
        for canon, _surf in mentions:
            member = self.team.resolve(canon)
            names.append((member.username or member.full_name) if member else canon.lstrip("@"))
        # запасной путь: «Имя: …» для незнакомого боту исполнителя
        if not names:
            prefix, _ = mp._match_assignee(correction, ctx)
            if prefix:
                member = self.team.resolve(prefix)
                names.append((member.username or member.full_name) if member else prefix.lstrip("@"))
                surfaces.append(prefix)
        if names:
            add_mode = any(k in correction.lower() for k in ("добав", "ещё", "еще", "также", "тоже", "подключи", "в помощь", "вместе с"))
            if add_mode:
                for n in names:
                    if task.add_assignee(n):
                        changed = True
            else:
                task.assignees = names
                changed = True

        # 5) Переформулировать заголовок — ТОЛЬКО если после отсева директив
        #    (приоритет/срок/исполнитель) осталась осмысленная новая формулировка.
        #    Так «повысь приоритет» и «назначь на Дашу» не перезаписывают название.
        if not new_title and mp.correction_is_reformulation(correction, surfaces):
            extracted = await self.provider.extract_tasks(correction, ctx)
            if extracted:
                ex = extracted[0]
                task.title = ex.task
                if ex.requirements:
                    task.requirements = ex.requirements
                changed = True

        if not changed:
            # Никакое поле не распозналось — трактуем правку как уточнение деталей
            task.requirements = correction.strip()

        task.confidence = max(task.confidence, 0.9)  # пользователь подтвердил вручную
        task.touch()
        return task

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
