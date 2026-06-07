"""Перечисления предметной области."""

from __future__ import annotations

from enum import Enum


class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"

    @property
    def emoji(self) -> str:
        return {"low": "🟢", "medium": "🟡", "high": "🔴"}[self.value]

    @property
    def label_ru(self) -> str:
        return {"low": "низкий", "medium": "средний", "high": "высокий"}[self.value]


class TaskStatus(str, Enum):
    todo = "todo"
    in_progress = "in_progress"
    done = "done"

    @property
    def label_ru(self) -> str:
        return {"todo": "К выполнению", "in_progress": "В работе", "done": "Готово"}[self.value]


class TaskSource(str, Enum):
    """Источник, из которого извлечена задача."""

    chat = "chat"          # текстовое сообщение в Telegram
    voice = "voice"        # голосовое сообщение / кружок
    meeting = "meeting"    # онлайн-встреча (Телемост)
    manual = "manual"      # заведена человеком вручную

    @property
    def label_ru(self) -> str:
        return {
            "chat": "чат",
            "voice": "голосовое",
            "meeting": "встреча",
            "manual": "вручную",
        }[self.value]


class ConfirmAction(str, Enum):
    """Действия в сценарии подтверждения отправки задачи на доску."""

    confirm = "confirm"
    edit = "edit"
    reject = "reject"
