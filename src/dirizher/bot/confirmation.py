"""Хранилище задач «в полёте» — между показом карточки и нажатием кнопки."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..domain.models import SourceRef, Task
from ..services.task_service import Outcome, ProcessedTask


@dataclass
class Pending:
    pid: str
    task: Task
    source: SourceRef
    outcome: Outcome
    chat_id: int
    duplicate_of_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PendingStore:
    """Краткоживущее состояние подтверждений (in-memory)."""

    def __init__(self) -> None:
        self._items: dict[str, Pending] = {}

    def put(self, p: ProcessedTask, chat_id: int) -> Pending:
        pid = uuid.uuid4().hex[:10]
        pending = Pending(
            pid=pid,
            task=p.task,
            source=p.task.sources[0],  # Task.from_extracted всегда кладёт источник
            outcome=p.outcome,
            chat_id=chat_id,
            duplicate_of_id=p.duplicate_of.id if p.duplicate_of else None,
        )
        self._items[pid] = pending
        return pending

    def get(self, pid: str) -> Pending | None:
        return self._items.get(pid)

    def pop(self, pid: str) -> Pending | None:
        return self._items.pop(pid, None)
