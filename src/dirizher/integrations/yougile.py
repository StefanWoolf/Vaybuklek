"""Клиент канбан-доски YouGile.

`MockBoard` — доска в памяти (карточки реально создаются/двигаются/закрываются,
видны через /board) — позволяет демонстрировать всю цепочку без ключа.
`YouGileBoard` — боевой REST-клиент (YouGile API v2).
Оба реализуют единый протокол `BoardClient`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

import httpx

from ..config import YouGileSettings
from ..domain.enums import TaskStatus
from ..domain.models import Task
from ..logging_setup import get_logger

log = get_logger("dirizher.yougile")


def _deadline_obj(d: date, t=None) -> dict:
    """YouGile API v2 ждёт дедлайн как объект с timestamp в миллисекундах.

    Если задано время суток (t: datetime.time) — ставим withTime=true.
    """
    from datetime import datetime

    hour, minute, with_time = (t.hour, t.minute, True) if t else (12, 0, False)
    ms = int(datetime(d.year, d.month, d.day, hour, minute).timestamp() * 1000)
    return {"deadline": ms, "withTime": with_time}


@dataclass
class BoardCard:
    id: str
    title: str
    status: TaskStatus = TaskStatus.todo
    assignees: list[str] = field(default_factory=list)
    deadline: date | None = None
    description: str = ""


@dataclass
class BoardUser:
    id: str
    email: str = ""
    name: str = ""


@runtime_checkable
class BoardClient(Protocol):
    name: str

    async def create_card(self, task: Task) -> str: ...
    async def move_card(self, card_id: str, status: TaskStatus) -> None: ...
    async def complete_card(self, card_id: str) -> None: ...
    async def update_card(self, card_id: str, task: Task) -> None: ...
    async def delete_card(self, card_id: str) -> None: ...
    async def list_cards(self) -> list[BoardCard]: ...
    async def list_users(self) -> list[BoardUser]: ...
    async def find_user_by_email(self, email: str) -> BoardUser | None: ...
    async def close(self) -> None: ...


class MockBoard:
    """Доска в памяти для mock-режима и тестов."""

    name = "mock"

    def __init__(self) -> None:
        self._cards: dict[str, BoardCard] = {}
        self._seq = 0
        # демо-пользователи доски для проверки привязки по email
        self._users: list[BoardUser] = []

    async def create_card(self, task: Task) -> str:
        self._seq += 1
        card_id = f"mock-{self._seq:04d}"
        self._cards[card_id] = BoardCard(
            id=card_id,
            title=task.title,
            status=task.status,
            assignees=list(task.assignees),
            deadline=task.deadline,
            description=task.requirements or "",
        )
        log.info("🗂️  [mock] карточка создана #%s: %s", card_id, task.title)
        return card_id

    async def move_card(self, card_id: str, status: TaskStatus) -> None:
        if card_id in self._cards:
            self._cards[card_id].status = status
            log.info("↔️  [mock] карточка #%s -> %s", card_id, status.label_ru)

    async def complete_card(self, card_id: str) -> None:
        await self.move_card(card_id, TaskStatus.done)

    async def update_card(self, card_id: str, task: Task) -> None:
        c = self._cards.get(card_id)
        if c:
            c.title = task.title
            c.assignees = list(task.assignees)
            c.deadline = task.deadline
            c.description = task.requirements or ""
            c.status = task.status
            log.info("✏️  [mock] карточка #%s обновлена: %s", card_id, task.title)

    async def delete_card(self, card_id: str) -> None:
        if self._cards.pop(card_id, None) is not None:
            log.info("🗑️  [mock] карточка #%s удалена", card_id)

    async def list_cards(self) -> list[BoardCard]:
        return list(self._cards.values())

    async def list_users(self) -> list[BoardUser]:
        return list(self._users)

    async def find_user_by_email(self, email: str) -> BoardUser | None:
        key = email.strip().lower()
        # в mock-режиме «привязываем» к псевдо-пользователю с этим email
        for u in self._users:
            if u.email.lower() == key:
                return u
        u = BoardUser(id=f"mock-user-{len(self._users) + 1}", email=email, name=email.split("@")[0])
        self._users.append(u)
        return u

    async def close(self) -> None:  # noqa: D401
        return None


class YouGileBoard:
    """Боевой клиент YouGile API v2."""

    name = "yougile"

    def __init__(self, cfg: YouGileSettings) -> None:
        self._cfg = cfg
        self._columns = {
            TaskStatus.todo: cfg.column_todo,
            TaskStatus.in_progress: cfg.column_in_progress,
            TaskStatus.done: cfg.column_done,
        }
        self._http = httpx.AsyncClient(
            base_url=cfg.base_url,
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            timeout=20.0,
        )
        self._users_cache: list[BoardUser] | None = None
        self._id_to_name: dict[str, str] = {}
        # обратная карта columnId -> TaskStatus, чтобы корректно читать колонку карточки
        self._col_to_status = {v: k for k, v in self._columns.items() if v}

    async def create_card(self, task: Task) -> str:
        payload: dict = {"title": task.title}
        col = self._columns.get(task.status)
        if col:
            payload["columnId"] = col
        if task.requirements:
            payload["description"] = task.requirements
        if task.deadline:
            payload["deadline"] = _deadline_obj(task.deadline, task.deadline_time)
        if task.board_assignee_ids:
            payload["assigned"] = list(task.board_assignee_ids)
        r = await self._http.post("/tasks", json=payload)
        r.raise_for_status()
        card_id = r.json().get("id", "")
        log.info("🗂️  карточка создана #%s: %s", card_id, task.title)
        return card_id

    async def move_card(self, card_id: str, status: TaskStatus) -> None:
        col = self._columns.get(status)
        body: dict = {}
        if col:
            body["columnId"] = col
        # снимаем/ставим флаг завершения в зависимости от колонки
        body["completed"] = status == TaskStatus.done
        r = await self._http.put(f"/tasks/{card_id}", json=body)
        r.raise_for_status()
        log.info("↔️  карточка #%s -> %s", card_id, status.label_ru)

    async def complete_card(self, card_id: str) -> None:
        col = self._columns.get(TaskStatus.done)
        body: dict = {"completed": True}
        if col:
            body["columnId"] = col
        r = await self._http.put(f"/tasks/{card_id}", json=body)
        r.raise_for_status()

    async def update_card(self, card_id: str, task: Task) -> None:
        body: dict = {"title": task.title}
        if task.requirements:
            body["description"] = task.requirements
        if task.deadline:
            body["deadline"] = _deadline_obj(task.deadline, task.deadline_time)
        col = self._columns.get(task.status)
        if col:
            body["columnId"] = col
        body["assigned"] = list(task.board_assignee_ids)
        r = await self._http.put(f"/tasks/{card_id}", json=body)
        r.raise_for_status()

    async def delete_card(self, card_id: str) -> None:
        # YouGile: «мягкое» удаление через флаг deleted
        r = await self._http.put(f"/tasks/{card_id}", json={"deleted": True})
        r.raise_for_status()
        log.info("🗑️  карточка #%s удалена", card_id)

    async def list_cards(self) -> list[BoardCard]:
        await self._ensure_users()
        r = await self._http.get("/tasks", params={"limit": 1000})
        r.raise_for_status()
        items = r.json().get("content", [])
        cards: list[BoardCard] = []
        for it in items:
            if it.get("deleted"):
                continue
            col_id = it.get("columnId", "")
            status = self._col_to_status.get(col_id, TaskStatus.todo)
            if it.get("completed"):
                status = TaskStatus.done
            assignees = [self._id_to_name.get(uid, uid) for uid in it.get("assigned", [])]
            cards.append(
                BoardCard(
                    id=it.get("id", ""),
                    title=it.get("title", ""),
                    status=status,
                    assignees=assignees,
                )
            )
        return cards

    async def _ensure_users(self) -> list[BoardUser]:
        if self._users_cache is None:
            r = await self._http.get("/users", params={"limit": 1000})
            r.raise_for_status()
            users: list[BoardUser] = []
            for it in r.json().get("content", []):
                u = BoardUser(
                    id=it.get("id", ""),
                    email=it.get("email", "") or "",
                    name=it.get("realName") or it.get("name") or "",
                )
                users.append(u)
                if u.id:
                    self._id_to_name[u.id] = u.name or u.email or u.id
            self._users_cache = users
        return self._users_cache

    async def list_users(self) -> list[BoardUser]:
        return await self._ensure_users()

    async def find_user_by_email(self, email: str) -> BoardUser | None:
        key = email.strip().lower()
        for u in await self._ensure_users():
            if u.email.lower() == key:
                return u
        return None

    async def close(self) -> None:
        await self._http.aclose()


def build_board(cfg: YouGileSettings) -> BoardClient:
    if cfg.is_mock:
        log.info("Канбан-доска: mock (в памяти)")
        return MockBoard()
    log.info("Канбан-доска: YouGile API")
    return YouGileBoard(cfg)
