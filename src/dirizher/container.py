"""Контейнер приложения — единая точка сборки зависимостей.

Используется ботом, планировщиком и HTTP-API, чтобы они работали поверх
одних и тех же сервисов и состояния.
"""

from __future__ import annotations

from .audio.embeddings import build_embedder
from .audio.speakers import SpeakerRegistry
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
from .services.gamification import GamificationService, GameStore
from .services.meeting import MeetingService
from .state_store import StateStore
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
        self.store = StateStore(s.memory.state_path)
        self.provider = build_provider(s)
        self.board = build_board(s.yougile)
        # Голосовые отпечатки: реестр + (опц.) эмбеддер для авто-имён спикеров.
        self.speakers = SpeakerRegistry(s.audio.voiceprints_path, s.audio.voiceprint_threshold)
        self.embedder = build_embedder(s.audio)
        self.transcriber = build_transcriber(
            s.audio,
            fallback_groq_keys=s.llm.groq_key_list,
            speaker_registry=self.speakers,
            embedder=self.embedder,
        )
        # Активные записи встреч (по чату): chat_id -> MeetingRecorder.
        self.active_meetings: dict[int, object] = {}

        self.service = TaskService(
            settings=s,
            provider=self.provider,
            board=self.board,
            memory=self.memory,
            snapshot=self.snapshot,
            repo=self.repo,
            team=self.team,
            persist=self.persist,
        )

        # Поднимаем сохранённое состояние (команда + задачи) после перезапуска.
        self._restore_state()
        # Геймификация (XP/уровни/ачивки/лидерборд, п.10) — начисление при закрытии задач.
        self.game = GamificationService(GameStore(s.memory.game_path), self.team)
        self.reconciliation = ReconciliationService(
            self.repo, self.team, self.service, game=self.game
        )
        self.meeting = MeetingService(self.service)
        self.mode = ModeStore(default_auto=False)
        self.pending = PendingStore()
        self.history = ChatHistory()

        # Заполняются на старте бота — нужны планировщику/API для отправки сообщений
        self.bot = None  # type: ignore[assignment]
        self.dp = None  # type: ignore[assignment]  # aiogram Dispatcher (для /ingest/telegram)

        log.info("Контейнер собран · режимы: %s", s.mode_banner())

    def _restore_state(self) -> None:
        members, tasks = self.store.load()
        for m in members:
            self.team.register(m)
        for t in tasks:
            self.repo.add(t)
            try:
                self.memory.remember(t.id, t.dedup_text())
            except Exception:  # noqa: BLE001
                pass

    def persist(self) -> None:
        """Сохранить команду и задачи на диск (вызывается после изменений)."""
        try:
            self.store.save(self.team.all(), self.repo.all())
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось сохранить состояние: %s", e)

    async def aclose(self) -> None:
        for rec in list(self.active_meetings.values()):
            try:
                rec.stop("manual")  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        self.persist()
        await self.board.close()
