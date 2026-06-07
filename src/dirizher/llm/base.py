"""Интерфейс LLM-провайдера для извлечения задач."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from ..domain.models import ExtractedTask, TeamMember


@dataclass
class ExtractionContext:
    """Контекст, передаваемый провайдеру для качественного извлечения."""

    today: date
    source_label: str = "чат"
    team: list[TeamMember] = field(default_factory=list)
    # Релевантный контекст из векторной памяти (история, решения)
    memory_context: str = ""
    # Недавняя переписка чата (последние реплики «Автор: текст») — для понимания
    # ссылок и команд «поставь таску» без деталей в самом сообщении.
    recent_dialog: list[str] = field(default_factory=list)

    def team_names(self) -> list[str]:
        names: list[str] = []
        for m in self.team:
            if m.full_name:
                names.append(m.full_name)
            if m.username:
                names.append(f"@{m.username}")
            names.extend(m.aliases)
        return names


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def extract_tasks(
        self, message: str, context: ExtractionContext
    ) -> list[ExtractedTask]:
        """Вернуть список задач (возможно пустой) в строгой схеме."""
        ...
