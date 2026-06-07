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


def build_transcriber(cfg: AudioSettings) -> Transcriber:
    if cfg.is_mock:
        log.info("Распознавание речи: mock (DIRIZHER_AUDIO__ENABLED=false)")
        return MockTranscriber()
    from .pipeline import WhisperPipeline  # ленивый импорт тяжёлых зависимостей

    log.info("Распознавание речи: noisereduce → pyannote → Whisper")
    return WhisperPipeline(cfg)
