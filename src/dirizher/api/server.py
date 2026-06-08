"""HTTP-API для оркестрации n8n.

Роль n8n (как в отчёте): принимать Telegram-webhook и cron-триггеры и
маршрутизировать их в ядро. Само ядро — это сервисы Дирижёра; API лишь
даёт n8n стабильные точки входа:

  POST /ingest/telegram          — n8n форвардит апдейт Telegram сюда
  POST /jobs/reminders           — cron: проверка дедлайнов и напоминания
  POST /jobs/evening-reconcile   — cron: вечерняя сверка отчётов
  GET  /health                   — статус и режимы компонентов

Защита: общий секрет в заголовке X-Dirizher-Token (если задан в .env).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException

from ..container import AppContainer
from ..logging_setup import get_logger
from ..scheduler.jobs import (
    run_evening_reconciliation,
    run_leaderboard_post,
    run_morning_digest,
    run_reminders,
)

log = get_logger("dirizher.api")


def create_api(container: AppContainer) -> FastAPI:
    app = FastAPI(title="Дирижёр API", version="0.1.0")
    secret = container.settings.api.shared_secret

    def _auth(token: str | None) -> None:
        if secret and token != secret:
            raise HTTPException(status_code=401, detail="invalid token")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "modes": container.settings.mode_banner(),
            "open_tasks": len(container.repo.open()),
            "memory_backend": container.memory.backend_name,
        }

    @app.post("/ingest/telegram")
    async def ingest_telegram(
        update: dict, x_dirizher_token: str | None = Header(default=None)
    ) -> dict[str, str]:
        _auth(x_dirizher_token)
        dp = getattr(container, "dp", None)
        if dp is None or container.bot is None:
            raise HTTPException(status_code=503, detail="bot not running")
        from aiogram.types import Update

        await dp.feed_update(container.bot, Update.model_validate(update))
        return {"status": "processed"}

    @app.post("/jobs/morning-digest")
    async def jobs_morning(x_dirizher_token: str | None = Header(default=None)) -> dict[str, int]:
        _auth(x_dirizher_token)
        chats = await run_morning_digest(container)
        return {"chats_notified": chats}

    @app.post("/jobs/reminders")
    async def jobs_reminders(x_dirizher_token: str | None = Header(default=None)) -> dict[str, int]:
        _auth(x_dirizher_token)
        sent = await run_reminders(container)
        return {"reminders_sent": sent}

    @app.post("/jobs/leaderboard")
    async def jobs_leaderboard(x_dirizher_token: str | None = Header(default=None)) -> dict[str, int]:
        _auth(x_dirizher_token)
        chats = await run_leaderboard_post(container)
        return {"chats_notified": chats}

    @app.post("/jobs/evening-reconcile")
    async def jobs_evening(x_dirizher_token: str | None = Header(default=None)) -> dict[str, int]:
        _auth(x_dirizher_token)
        chats = await run_evening_reconciliation(container)
        return {"chats_notified": chats}

    return app
