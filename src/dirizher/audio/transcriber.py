"""Интерфейс распознавания речи + mock.

Боевой пайплайн (noisereduce → pyannote → Whisper) — в pipeline.py и включается
флагом DIRIZHER_AUDIO__ENABLED. Mock честно сообщает, что распознавание выключено,
сохраняя работоспособность остальной системы.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..config import AudioSettings
from ..logging_setup import get_logger

log = get_logger("dirizher.audio")


@dataclass
class Segment:
    speaker: str
    text: str


@dataclass
class TranscriptResult:
    text: str
    segments: list[Segment] = field(default_factory=list)
    is_mock: bool = False


@runtime_checkable
class Transcriber(Protocol):
    name: str

    async def transcribe(self, file_path: str) -> TranscriptResult: ...


class MockTranscriber:
    name = "mock"

    async def transcribe(self, file_path: str) -> TranscriptResult:
        log.info("[mock] распознавание пропущено: %s", file_path)
        return TranscriptResult(text="", segments=[], is_mock=True)


def build_transcriber(
    cfg: AudioSettings,
    fallback_groq_keys: list[str] | None = None,
    *,
    speaker_registry=None,
    embedder=None,
) -> Transcriber:
    if cfg.is_mock:
        log.info("Распознавание речи: mock (DIRIZHER_AUDIO__ENABLED=false)")
        return MockTranscriber()

    if cfg.backend == "groq":
        # Свои ключи аудио или, если их нет, переиспользуем ключи LLM-провайдера.
        keys = cfg.groq_key_list or list(fallback_groq_keys or [])
        if not keys:
            log.warning("Groq Whisper включён, но ключей нет — откатываюсь в mock")
            return MockTranscriber()
        from .groq_transcriber import GroqWhisperTranscriber

        log.info("Распознавание речи: Groq Whisper (%s)", cfg.groq_whisper_model)
        return GroqWhisperTranscriber(keys, cfg.groq_whisper_model)

    from .pipeline import WhisperPipeline  # ленивый импорт тяжёлых зависимостей

    log.info("Распознавание речи: noisereduce → pyannote → faster-whisper (local)")
    return WhisperPipeline(cfg, speaker_registry=speaker_registry, embedder=embedder)
