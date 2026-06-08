"""Боевой провайдер Groq (Llama 3.3 70B) с ротацией нескольких ключей.

Дневной лимит токенов (TPD) у Groq на ключ. Если ключ исчерпан (429), провайдер
прозрачно переключается на следующий ключ и запоминает рабочий индекс, чтобы не
тратить вызовы на заведомо исчерпанный ключ. Когда исчерпаны ВСЕ ключи —
поднимаем исключение (TaskService откатится на эвристику).
"""

from __future__ import annotations

from ..domain.models import ExtractedTask
from ..logging_setup import get_logger
from .base import ExtractionContext
from .parsing import parse_tasks
from .prompt import SYSTEM_PROMPT, build_user_prompt

log = get_logger("dirizher.llm.groq")


class GroqLLMProvider:
    name = "groq"

    def __init__(self, api_keys: list[str], model: str) -> None:
        from groq import AsyncGroq  # ленивый импорт

        self._clients = [AsyncGroq(api_key=k) for k in api_keys]
        self._model = model
        self._idx = 0  # индекс текущего рабочего ключа
        log.info("Groq: ключей в ротации — %d", len(self._clients))

    async def extract_tasks(
        self, message: str, context: ExtractionContext
    ) -> list[ExtractedTask]:
        from groq import RateLimitError

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(message, context)},
        ]
        last_err: Exception | None = None
        for _ in range(len(self._clients)):
            client = self._clients[self._idx]
            try:
                resp = await client.chat.completions.create(
                    model=self._model,
                    temperature=0.1,
                    response_format={"type": "json_object"},
                    messages=messages,
                )
                return parse_tasks(resp.choices[0].message.content or "")
            except RateLimitError as e:
                last_err = e
                n = len(self._clients)
                log.warning("Groq ключ #%d/%d исчерпан (429) — переключаюсь", self._idx + 1, n)
                self._idx = (self._idx + 1) % n
                continue
        # все ключи исчерпаны
        raise last_err if last_err else RuntimeError("Groq: нет доступных ключей")
