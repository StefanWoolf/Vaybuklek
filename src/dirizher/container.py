"""Контейнер приложения — единая точка сборки зависимостей.

Используется ботом, планировщиком и HTTP-API, чтобы они работали поверх
одних и тех же сервисов и состояния.
"""

from __future__ import annotations

from .audio.transcriber import build_transcriber
from .bot.confirmation import PendingStore
from .chat_history import ChatHistory
from .config import Settings, get_settings
from .integrations.yougile import build_board
from .llm.extractor import build_provider
from .logging_setup import get_logger
from .memory.project_snapshot import ProjectSnapshot
from .memory.vector_store import TaskMemory
from .repository import TaskRepository, TeamRegistry
from .services.meeting import MeetingService
from .services.reconciliation import ReconciliationService
from .services.task_service import TaskService

log = get_logger("dirizher.container")


class ModeStore:
    """Флаг авто-режима отправки задач на доску (per-chat).

    True  — задачи уходят на доску сразу, без вопросов.
    False — каждый раз спрашиваем подтверждение и предлагаем правку.
    По умолчанию False (надёжность важнее скорости).
    """

    def __init__(self, default_auto: bool = False) -> None:
        self._default = default_auto
        self._by_chat: dict[int, bool] = {}

    def is_auto(self, chat_id: int) -> bool:
        return self._by_chat.get(chat_id, self._default)

    def set_auto(self, chat_id: int, value: bool) -> None:
        self._by_chat[chat_id] = value


class AppContainer:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        s = self.settings

        self.repo = TaskRepository()
        self.team = TeamRegistry()
        self.memory = TaskMemory(
            s.memory.chroma_path, s.memory.dedup_threshold, backend=s.memory.backend
        )
        self.snapshot = ProjectSnapshot(s.memory.project_snapshot)
        self.provider = build_provider(s)
        self.board = build_board(s.yougile)
        self.transcriber = build_transcriber(s.audio)

        self.service = TaskService(
            settings=s,
            provider=self.provider,
            board=self.board,
            memory=self.memory,
            snapshot=self.snapshot,
            repo=self.repo,
            team=self.team,
        )
        self.reconciliation = ReconciliationService(self.repo, self.team, self.service)
        self.meeting = MeetingService(self.service)
        self.mode = ModeStore(default_auto=False)
        self.pending = PendingStore()
        self.history = ChatHistory()

        # Заполняются на старте бота — нужны планировщику/API для отправки сообщений
        self.bot = None  # type: ignore[assignment]
        self.dp = None  # type: ignore[assignment]  # aiogram Dispatcher (для /ingest/telegram)

        log.info("Контейнер собран · режимы: %s", s.mode_banner())

    async def aclose(self) -> None:
        await self.board.close()
