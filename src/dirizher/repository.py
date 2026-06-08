"""Хранилище задач и реестр команды (in-memory).

Намеренно простое и потокобезопасное на уровне asyncio (однопоточный event loop).
Легко заменяется на БД за тем же интерфейсом.
"""

from __future__ import annotations

from datetime import date

from .domain.enums import TaskStatus
from .domain.models import Task, TeamMember


class TaskRepository:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def add(self, task: Task) -> Task:
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def get_by_card(self, card_id: str) -> Task | None:
        for t in self._tasks.values():
            if t.board_card_id == card_id:
                return t
        return None

    def last_created(self) -> Task | None:
        return max(self._tasks.values(), key=lambda t: t.created_at, default=None)

    def all(self) -> list[Task]:
        return list(self._tasks.values())

    def open(self) -> list[Task]:
        return [t for t in self._tasks.values() if t.status != TaskStatus.done]

    def by_assignee(self, name: str) -> list[Task]:
        key = name.lstrip("@").lower()
        return [
            t for t in self._tasks.values()
            if t.assignee and t.assignee.lstrip("@").lower() == key
        ]

    def open_by_assignee(self, name: str) -> list[Task]:
        return [t for t in self.by_assignee(name) if t.status != TaskStatus.done]

    def due_on_or_before(self, day: date) -> list[Task]:
        return [
            t for t in self._tasks.values()
            if t.deadline and t.deadline <= day and t.status != TaskStatus.done
        ]

    def remove(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)


class TeamRegistry:
    """Реестр участников. Резолвит имя/алиас/username из текста в участника."""

    def __init__(self) -> None:
        self._by_id: dict[int, TeamMember] = {}
        self._anon: list[TeamMember] = []  # без user_id (упомянуты, но не в чате)

    def register(self, member: TeamMember) -> TeamMember:
        if member.user_id is not None:
            existing = self._by_id.get(member.user_id)
            if existing:
                existing.username = member.username or existing.username
                existing.full_name = member.full_name or existing.full_name
                existing.email = member.email or existing.email
                existing.yougile_id = member.yougile_id or existing.yougile_id
                for a in member.aliases:
                    if a not in existing.aliases:
                        existing.aliases.append(a)
                return existing
            self._by_id[member.user_id] = member
            return member
        self._anon.append(member)
        return member

    def knows(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self._by_id

    def clear(self) -> int:
        """Забыть всех участников (имена, алиасы, email, привязки к доске)."""
        n = len(self._by_id) + len(self._anon)
        self._by_id.clear()
        self._anon.clear()
        return n

    def all(self) -> list[TeamMember]:
        return list(self._by_id.values()) + self._anon

    @staticmethod
    def _candidates(m: TeamMember) -> list[str]:
        cands = [m.username or "", m.full_name, *m.aliases]
        if m.full_name:
            cands.append(m.full_name.split()[0])  # имя без фамилии
        return [c.lower() for c in cands if c]

    def resolve(self, name: str | None) -> TeamMember | None:
        """Найти участника по username/имени/алиасу (регистронезависимо). Первый матч."""
        matches = self.resolve_all(name)
        return matches[0] if matches else None

    def resolve_all(self, name: str | None) -> list[TeamMember]:
        """Все участники, подходящие под имя/алиас (для разрешения тёзок, #6)."""
        if not name:
            return []
        key = name.lstrip("@").strip().lower()
        if not key:
            return []
        return [m for m in self.all() if key in self._candidates(m)]

    def mention_for(self, name: str | None) -> str:
        """Готовая @-упоминалка для исполнителя (или просто текстом)."""
        m = self.resolve(name)
        if m:
            return m.mention()
        return f"@{name.lstrip('@')}" if name else "—"
