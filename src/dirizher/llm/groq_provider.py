"""Боевой провайдер Groq (Llama 3.3 70B). Зависимости импортируются лениво."""

from __future__ import annotations

from ..domain.models import ExtractedTask
from ..logging_setup import get_logger
from .base import ExtractionContext
from .parsing import parse_tasks
from .prompt import SYSTEM_PROMPT, build_user_prompt

log = get_logger("dirizher.llm.groq")


class GroqLLMProvider:
    name = "groq"

    def __init__(self, api_key: str, model: str) -> None:
        from groq import AsyncGroq  # ленивый импорт

        self._client = AsyncGroq(api_key=api_key)
        self._model = model

    async def extract_tasks(
        self, message: str, context: ExtractionContext
    ) -> list[ExtractedTask]:
        resp = await self._client.chat.completions.create(
            model=self._model,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(message, context)},
            ],
        )
        raw = resp.choices[0].message.content or ""
        return parse_tasks(raw)
