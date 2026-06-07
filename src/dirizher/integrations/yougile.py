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
    assignee: str | None = None
    deadline: date | None = None
    description: str = ""


@runtime_checkable
class BoardClient(Protocol):
    name: str

    async def create_card(self, task: Task) -> str: ...
    async def move_card(self, card_id: str, status: TaskStatus) -> None: ...
    async def complete_card(self, card_id: str) -> None: ...
    async def update_card(self, card_id: str, task: Task) -> None: ...
    async def list_cards(self) -> list[BoardCard]: ...
    async def close(self) -> None: ...


class MockBoard:
    """Доска в памяти для mock-режима и тестов."""

    name = "mock"

    def __init__(self) -> None:
        self._cards: dict[str, BoardCard] = {}
        self._seq = 0

    async def create_card(self, task: Task) -> str:
        self._seq += 1
        card_id = f"mock-{self._seq:04d}"
        self._cards[card_id] = BoardCard(
            id=card_id,
            title=task.title,
            status=task.status,
            assignee=task.assignee,
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
            c.assignee = task.assignee
            c.deadline = task.deadline
            c.description = task.requirements or ""
            c.status = task.status
            log.info("✏️  [mock] карточка #%s обновлена: %s", card_id, task.title)

    async def list_cards(self) -> list[BoardCard]:
        return list(self._cards.values())

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

    async def create_card(self, task: Task) -> str:
        payload: dict = {"title": task.title}
        col = self._columns.get(task.status)
        if col:
            payload["columnId"] = col
        if task.requirements:
            payload["description"] = task.requirements
        if task.deadline:
            payload["deadline"] = _deadline_obj(task.deadline, task.deadline_time)
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
        if status == TaskStatus.done:
            body["completed"] = True
        r = await self._http.put(f"/tasks/{card_id}", json=body)
        r.raise_for_status()

    async def complete_card(self, card_id: str) -> None:
        r = await self._http.put(f"/tasks/{card_id}", json={"completed": True})
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
        r = await self._http.put(f"/tasks/{card_id}", json=body)
        r.raise_for_status()

    async def list_cards(self) -> list[BoardCard]:
        r = await self._http.get("/tasks", params={"limit": 1000})
        r.raise_for_status()
        items = r.json().get("content", [])
        cards: list[BoardCard] = []
        for it in items:
            status = TaskStatus.done if it.get("completed") else TaskStatus.todo
            cards.append(BoardCard(id=it.get("id", ""), title=it.get("title", ""), status=status))
        return cards

    async def close(self) -> None:
        await self._http.aclose()


def build_board(cfg: YouGileSettings) -> BoardClient:
    if cfg.is_mock:
        log.info("Канбан-доска: mock (в памяти)")
        return MockBoard()
    log.info("Канбан-доска: YouGile API")
    return YouGileBoard(cfg)
