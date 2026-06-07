"""Аудио-слой: распознавание речи со встреч и голосовых (Этап 3)."""

from .transcriber import Transcriber, TranscriptResult, build_transcriber

__all__ = ["Transcriber", "TranscriptResult", "build_transcriber"]
