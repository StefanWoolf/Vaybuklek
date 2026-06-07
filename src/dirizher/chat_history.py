"""Скользящее окно последних сообщений чата — контекст для LLM.

Позволяет боту собирать задачу из обсуждения: например, команда «поставь таску»
без деталей опирается на недавнюю переписку («созвон в четверг в 20:00»).
"""

from __future__ import annotations

from collections import defaultdict, deque


class ChatHistory:
    def __init__(self, maxlen: int = 20) -> None:
        self._buf: dict[int, deque[tuple[str, str]]] = defaultdict(lambda: deque(maxlen=maxlen))

    def add(self, chat_id: int, author: str, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._buf[chat_id].append((author or "—", text))

    def recent(self, chat_id: int, limit: int = 12, *, exclude_last: bool = False) -> list[str]:
        """Последние реплики в формате «Автор: текст»."""
        items = list(self._buf[chat_id])
        if exclude_last and items:
            items = items[:-1]
        return [f"{a}: {t}" for a, t in items[-limit:]]
