"""Боевой пайплайн распознавания речи: noisereduce → pyannote → Whisper.

Тяжёлые зависимости (faster-whisper, noisereduce, pyannote, soundfile, numpy)
импортируются лениво и нужны только при DIRIZHER_AUDIO__ENABLED=true
(extra-зависимости `audio`). Без них система работает через MockTranscriber.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ..config import AudioSettings
from ..logging_setup import get_logger
from .transcriber import Segment, TranscriptResult

log = get_logger("dirizher.audio.pipeline")


class WhisperPipeline:
    name = "whisper"

    def __init__(self, cfg: AudioSettings) -> None:
        self._cfg = cfg
        self._model = None
        self._diarizer = None

    # ── ленивая инициализация моделей ────────────────────────────────────────
    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("Загружаю Whisper (%s)…", self._cfg.whisper_model)
            self._model = WhisperModel(self._cfg.whisper_model, device="auto", compute_type="int8")
        return self._model

    def _ensure_diarizer(self):
        if self._diarizer is None and self._cfg.hf_token:
            try:
                from pyannote.audio import Pipeline

                log.info("Загружаю pyannote 3.1 (диаризация)…")
                self._diarizer = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1", use_auth_token=self._cfg.hf_token
                )
            except Exception as e:  # noqa: BLE001
                log.warning("Диаризация недоступна (%s) — продолжаю без неё", e)
        return self._diarizer

    # ── публичный API ────────────────────────────────────────────────────────
    async def transcribe(self, file_path: str) -> TranscriptResult:
        return await asyncio.to_thread(self._transcribe_sync, file_path)

    def _transcribe_sync(self, file_path: str) -> TranscriptResult:
        clean_path = self._denoise(file_path)
        segments = self._whisper_segments(clean_path)
        diar = self._diarize(clean_path)
        if diar:
            for seg in segments:
                seg.speaker = self._speaker_at(diar, seg)
        text = " ".join(s.text for s in segments).strip()
        return TranscriptResult(text=text, segments=segments, is_mock=False)

    # ── шаги пайплайна ───────────────────────────────────────────────────────
    def _denoise(self, file_path: str) -> str:
        """Шумоподавление перед транскрипцией (+качество, см. отчёт 3.1)."""
        try:
            import noisereduce as nr
            import soundfile as sf

            data, rate = sf.read(file_path)
            reduced = nr.reduce_noise(y=data, sr=rate)
            out = str(Path(tempfile.gettempdir()) / f"dz_clean_{Path(file_path).stem}.wav")
            sf.write(out, reduced, rate)
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("Шумоподавление пропущено (%s)", e)
            return file_path

    def _whisper_segments(self, file_path: str) -> list:
        model = self._ensure_model()
        raw_segments, _info = model.transcribe(file_path, language="ru", vad_filter=True)
        result = []
        for s in raw_segments:
            result.append(_TimedSegment(start=s.start, end=s.end, text=s.text.strip()))
        return result

    def _diarize(self, file_path: str):
        diarizer = self._ensure_diarizer()
        if diarizer is None:
            return None
        annotation = diarizer(file_path)
        turns = []
        for turn, _track, speaker in annotation.itertracks(yield_label=True):
            turns.append((turn.start, turn.end, speaker))
        return turns

    @staticmethod
    def _speaker_at(turns, seg) -> str:
        """Спикер с максимальным временным перекрытием для сегмента."""
        best, best_ov = "Speaker_1", 0.0
        for start, end, speaker in turns:
            ov = max(0.0, min(seg.end, end) - max(seg.start, start))
            if ov > best_ov:
                best, best_ov = speaker, ov
        return best


class _TimedSegment(Segment):
    """Сегмент с таймкодами (наследует speaker/text)."""

    def __init__(self, start: float, end: float, text: str, speaker: str = "Speaker_1") -> None:
        super().__init__(speaker=speaker, text=text)
        self.start = start
        self.end = end
