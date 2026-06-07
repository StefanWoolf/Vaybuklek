"""Боевой провайдер GigaChat (Sber). Зависимости импортируются лениво."""

from __future__ import annotations

import asyncio

from ..domain.models import ExtractedTask
from ..logging_setup import get_logger
from .base import ExtractionContext
from .parsing import parse_tasks
from .prompt import SYSTEM_PROMPT, build_user_prompt

log = get_logger("dirizher.llm.gigachat")


class GigaChatLLMProvider:
    name = "gigachat"

    def __init__(self, credentials: str, scope: str) -> None:
        from gigachat import GigaChat  # ленивый импорт

        # verify_ssl_certs=False — типично для self-signed цепочки НУЦ Минцифры
        self._client = GigaChat(credentials=credentials, scope=scope, verify_ssl_certs=False)

    async def extract_tasks(
        self, message: str, context: ExtractionContext
    ) -> list[ExtractedTask]:
        from gigachat.models import Chat, Messages, MessagesRole

        chat = Chat(
            messages=[
                Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
                Messages(role=MessagesRole.USER, content=build_user_prompt(message, context)),
            ],
            temperature=0.1,
        )
        # SDK синхронный — уводим в тред, чтобы не блокировать event loop
        resp = await asyncio.to_thread(self._client.chat, chat)
        raw = resp.choices[0].message.content or ""
        return parse_tasks(raw)
