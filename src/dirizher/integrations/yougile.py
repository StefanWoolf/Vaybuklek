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
    assignee_ids: list[str] = field(default_factory=list)


@runtime_checkable
class BoardClient(Protocol):
    name: str

    async def create_card(self, task: Task) -> str: ...
    async def move_card(self, card_id: str, status: TaskStatus) -> None: ...
    async def complete_card(self, card_id: str) -> None: ...
    async def update_card(self, card_id: str, task: Task) -> None: ...
    async def delete_card(self, card_id: str) -> None: ...
    async def find_user_by_email(self, email: str) -> tuple[str, str] | None: ...
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
            assignee_ids=list(task.assignee_yougile_ids),
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
            c.assignee_ids = list(task.assignee_yougile_ids)
            log.info("✏️  [mock] карточка #%s обновлена: %s", card_id, task.title)

    async def delete_card(self, card_id: str) -> None:
        if self._cards.pop(card_id, None):
            log.info("🗑️  [mock] карточка #%s удалена", card_id)

    async def find_user_by_email(self, email: str) -> tuple[str, str] | None:
        # mock-доска без реальных пользователей — привязки нет
        return None

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
        # обратная карта columnId -> статус, чтобы /board показывал реальную колонку
        self._status_by_col = {v: k for k, v in self._columns.items() if v}
        self._users: dict[str, str] | None = None  # кэш id->имя пользователя доски
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
        if task.assignee_yougile_ids:
            payload["assigned"] = task.assignee_yougile_ids
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
        # completed синхронизируем со статусом: done → True, иначе снимаем флаг
        body["completed"] = status == TaskStatus.done
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
        if task.assignee_yougile_ids:
            body["assigned"] = task.assignee_yougile_ids
        r = await self._http.put(f"/tasks/{card_id}", json=body)
        r.raise_for_status()

    async def delete_card(self, card_id: str) -> None:
        r = await self._http.put(f"/tasks/{card_id}", json={"deleted": True})
        r.raise_for_status()

    async def find_user_by_email(self, email: str) -> tuple[str, str] | None:
        """Найти пользователя доски по email → (id, имя). Иначе None."""
        target = email.strip().lower()
        try:
            r = await self._http.get("/users", params={"limit": 1000})
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            log.warning("YouGile /users недоступен: %s", e)
            return None
        for u in r.json().get("content", []):
            if (u.get("email") or "").strip().lower() == target:
                name = u.get("realName") or u.get("name") or email
                return u.get("id", ""), name
        return None

    async def _users_map(self) -> dict[str, str]:
        """id пользователя доски → отображаемое имя (кэшируется на сессию)."""
        if self._users is not None:
            return self._users
        users: dict[str, str] = {}
        try:
            r = await self._http.get("/users", params={"limit": 1000})
            r.raise_for_status()
            for u in r.json().get("content", []):
                users[u.get("id", "")] = u.get("realName") or u.get("name") or u.get("email") or "?"
        except Exception as e:  # noqa: BLE001
            log.warning("YouGile /users недоступен для имён исполнителей: %s", e)
        self._users = users
        return users

    async def list_cards(self) -> list[BoardCard]:
        from datetime import datetime

        r = await self._http.get("/tasks", params={"limit": 1000})
        r.raise_for_status()
        items = r.json().get("content", [])
        users = await self._users_map()
        cards: list[BoardCard] = []
        for it in items:
            if it.get("deleted"):
                continue
            # /tasks возвращает задачи ВСЕХ досок компании — берём только наши три
            # колонки, иначе в доску подмешиваются чужие карточки.
            col = it.get("columnId", "")
            if col not in self._status_by_col:
                continue
            # статус берём по реальной колонке; completed имеет приоритет «Готово»
            status = self._status_by_col[col]
            if it.get("completed"):
                status = TaskStatus.done
            # исполнители: id -> имя
            assigned = it.get("assigned") or []
            names = [users.get(uid, "") for uid in assigned]
            assignee = ", ".join(n for n in names if n) or None
            # дедлайн (object {deadline: ms} либо null)
            deadline = None
            dl = it.get("deadline")
            if isinstance(dl, dict) and dl.get("deadline"):
                try:
                    deadline = datetime.fromtimestamp(dl["deadline"] / 1000).date()
                except Exception:  # noqa: BLE001
                    deadline = None
            cards.append(
                BoardCard(
                    id=it.get("id", ""),
                    title=it.get("title", ""),
                    status=status,
                    assignee=assignee,
                    deadline=deadline,
                    assignee_ids=list(assigned),
                )
            )
        return cards

    async def close(self) -> None:
        await self._http.aclose()


def build_board(cfg: YouGileSettings) -> BoardClient:
    if cfg.is_mock:
        log.info("Канбан-доска: mock (в памяти)")
        return MockBoard()
    log.info("Канбан-доска: YouGile API")
    return YouGileBoard(cfg)
