"""Распознавание речи через Groq Whisper API (без локальных моделей).

Groq хостит Whisper (`whisper-large-v3-turbo`) — это быстрый облачный STT,
который не требует ffmpeg/torch/faster-whisper на машине. Аудио из Telegram
(.oga/.ogg/.mp4) Groq принимает как есть. Ключи переиспользуются те же, что и
для LLM, с такой же ротацией при 429 (см. GroqLLMProvider).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from ..logging_setup import get_logger
from .transcriber import TranscriptResult

log = get_logger("dirizher.audio.groq")


class GroqWhisperTranscriber:
    name = "groq-whisper"

    def __init__(self, api_keys: list[str], model: str) -> None:
        from groq import AsyncGroq  # ленивый импорт

        self._clients = [AsyncGroq(api_key=k) for k in api_keys]
        self._model = model
        self._idx = 0  # индекс текущего рабочего ключа
        log.info("Groq Whisper (%s): ключей в ротации — %d", model, len(self._clients))

    async def transcribe(self, file_path: str) -> TranscriptResult:
        from groq import RateLimitError

        data = await asyncio.to_thread(Path(file_path).read_bytes)
        filename = os.path.basename(file_path) or "audio.ogg"

        last_err: Exception | None = None
        for _ in range(len(self._clients)):
            client = self._clients[self._idx]
            try:
                resp = await client.audio.transcriptions.create(
                    file=(filename, data),
                    model=self._model,
                    language="ru",
                )
                text = (getattr(resp, "text", None) or "").strip()
                return TranscriptResult(text=text, is_mock=False)
            except RateLimitError as e:
                last_err = e
                n = len(self._clients)
                log.warning("Groq Whisper ключ #%d/%d исчерпан (429) — переключаюсь", self._idx + 1, n)
                self._idx = (self._idx + 1) % n
                continue
        raise last_err if last_err else RuntimeError("Groq Whisper: нет доступных ключей")
