"""Доменные модели Дирижёра (pydantic v2).

`ExtractedTask` — «сырой» результат LLM в строгой схеме (с полем confidence).
`Task` — доменная задача с идентификатором, статусом, источниками и связью
с карточкой на доске YouGile.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from html import escape as _esc

from pydantic import BaseModel, Field, field_validator

from .enums import Priority, TaskSource, TaskStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class ExtractedTask(BaseModel):
    """Строгая схема, которую обязан вернуть LLM для каждой задачи.

    Поле `confidence` — ключевое: при значении ниже порога бот не создаёт
    карточку молча, а уточняет у участника (убирает фантомные задачи).
    """

    task: str = Field(..., description="Краткая формулировка задачи")
    assignee: str | None = Field(None, description="Имя/username исполнителя или null")
    deadline: date | None = Field(None, description="Дедлайн в формате YYYY-MM-DD или null")
    deadline_time: time | None = Field(None, description="Время суток HH:MM, если указано, иначе null")
    priority: Priority = Priority.medium
    confidence: float = Field(..., ge=0.0, le=1.0)
    requirements: str | None = Field(None, description="Доп. детали/критерии (необязательно)")

    @field_validator("task")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("task не может быть пустым")
        return v


class TeamMember(BaseModel):
    """Участник команды. `aliases` — имена, которыми его зовут в чате/на встрече,
    чтобы LLM-исполнитель «Алексей» мэпился на конкретного человека."""

    user_id: int | None = None
    username: str | None = None  # без ведущего @
    full_name: str = ""
    aliases: list[str] = Field(default_factory=list)
    email: str | None = None          # email, привязанный к доске YouGile
    yougile_id: str | None = None     # id пользователя на доске YouGile
    voice_registered: bool = False

    def mention(self) -> str:
        """HTML-упоминание (бот работает в parse_mode=HTML)."""
        if self.username:
            return f"@{self.username}"
        if self.user_id:
            name = _esc(self.full_name or "участник")
            return f'<a href="tg://user?id={self.user_id}">{name}</a>'
        return _esc(self.full_name or "участник")


class SourceRef(BaseModel):
    """Ссылка на первоисточник задачи — для связности (чат + встреча → одна карточка)."""

    source: TaskSource
    chat_id: int | None = None
    message_id: int | None = None
    excerpt: str = ""
    captured_at: datetime = Field(default_factory=_utcnow)


class Task(BaseModel):
    """Доменная задача. Живёт в Repository, отражается карточкой на доске."""

    id: str = Field(default_factory=_new_id)
    title: str
    requirements: str | None = None
    assignee: str | None = None  # отображаемая строка (может быть несколько через «, »)
    assignee_yougile_ids: list[str] = Field(default_factory=list)  # id пользователей YouGile
    deadline: date | None = None
    deadline_time: time | None = None  # время суток, если указано
    priority: Priority = Priority.medium
    status: TaskStatus = TaskStatus.todo

    confidence: float = 1.0
    sources: list[SourceRef] = Field(default_factory=list)

    board_card_id: str | None = None  # id карточки в YouGile
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    reminded_at: datetime | None = None

    @classmethod
    def from_extracted(cls, ex: ExtractedTask, source: SourceRef) -> "Task":
        return cls(
            title=ex.task,
            requirements=ex.requirements,
            assignee=ex.assignee,
            deadline=ex.deadline,
            deadline_time=ex.deadline_time,
            priority=ex.priority,
            confidence=ex.confidence,
            sources=[source],
        )

    def deadline_display(self) -> str:
        """Человекочитаемый дедлайн с днём недели и временем (если есть)."""
        if not self.deadline:
            return "без срока"
        wd = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        out = f"{self.deadline.isoformat()} ({wd[self.deadline.weekday()]})"
        if self.deadline_time:
            out += f" в {self.deadline_time.strftime('%H:%M')}"
        return out

    def dedup_text(self) -> str:
        """Текст для векторного поиска дублей."""
        parts = [self.title]
        if self.requirements:
            parts.append(self.requirements)
        return " — ".join(parts)

    def touch(self) -> None:
        self.updated_at = _utcnow()


class ConfirmationDecision(BaseModel):
    """Результат сценария подтверждения (для журналирования/тестов)."""

    task_id: str
    accepted: bool
    auto: bool  # True => режим авто-отправки, подтверждение не запрашивалось
