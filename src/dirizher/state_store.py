"""Персистентное состояние: команда + задачи в одном JSON-файле.

Зачем: TeamRegistry и TaskRepository живут в памяти, поэтому после перезапуска
бот «забывал» почту, алиасы и привязки к доске. Этот стор сериализует команду и
задачи и поднимает их обратно на старте — без БД, атомарной записью.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .domain.models import Task, TeamMember
from .logging_setup import get_logger

log = get_logger("dirizher.state")


class StateStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> tuple[list[TeamMember], list[Task]]:
        if not self._path.exists():
            return [], []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось прочитать состояние (%s) — старт с чистого", e)
            return [], []
        members: list[TeamMember] = []
        for m in data.get("team", []):
            try:
                members.append(TeamMember(**m))
            except Exception as e:  # noqa: BLE001
                log.warning("Пропущен участник из состояния: %s", e)
        tasks: list[Task] = []
        for t in data.get("tasks", []):
            try:
                tasks.append(Task(**t))
            except Exception as e:  # noqa: BLE001
                log.warning("Пропущена задача из состояния: %s", e)
        log.info("Состояние загружено: участников %d, задач %d", len(members), len(tasks))
        return members, tasks

    def save(self, members: list[TeamMember], tasks: list[Task]) -> None:
        data = {
            "team": [m.model_dump(mode="json") for m in members],
            "tasks": [t.model_dump(mode="json") for t in tasks],
        }
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        # атомарно: пишем во временный файл и заменяем — не оставляем «битый» файл
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self._path)
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось сохранить состояние: %s", e)
            if os.path.exists(tmp):
                os.unlink(tmp)
