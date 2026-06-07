"""Боевой пайплайн распознавания речи из Telegram.

Поток:
Telegram .oga/.mp4/.ogg -> ffmpeg -> wav 16 kHz mono -> noisereduce -> faster-whisper.
Диаризация через pyannote остаётся опциональной: если HF-токена нет, работаем без неё.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
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

    async def transcribe(self, file_path: str) -> TranscriptResult:
        return await asyncio.to_thread(self._transcribe_sync, file_path)

    def _transcribe_sync(self, file_path: str) -> TranscriptResult:
        created_files: list[str] = []

        try:
            wav_path = self._to_wav(file_path, created_files)
            clean_path = self._denoise(wav_path, created_files)

            segments = self._whisper_segments(clean_path)
            diar = self._diarize(clean_path)

            if diar:
                for seg in segments:
                    seg.speaker = self._speaker_at(diar, seg)

            text = " ".join(s.text for s in segments).strip()
            return TranscriptResult(text=text, segments=segments, is_mock=False)

        finally:
            for path in created_files:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass

    # ── модели ───────────────────────────────────────────────────────────────

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("Загружаю Whisper-модель: %s", self._cfg.whisper_model)

            self._model = WhisperModel(
                self._cfg.whisper_model,
                device="cpu",
                compute_type="int8",
            )

        return self._model

    def _ensure_diarizer(self):
        if self._diarizer is None and self._cfg.hf_token:
            try:
                from pyannote.audio import Pipeline

                log.info("Загружаю pyannote для диаризации")
                self._diarizer = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    use_auth_token=self._cfg.hf_token,
                )
            except Exception as e:
                log.warning("Диаризация недоступна: %s", e)
                self._diarizer = None

        return self._diarizer

    # ── подготовка аудио ─────────────────────────────────────────────────────

    def _to_wav(self, file_path: str, created_files: list[str]) -> str:
        """Конвертирует Telegram voice/video_note в WAV.

        Telegram voice обычно приходит как .oga/.ogg с Opus.
        video_note приходит как .mp4.
        Whisper это часто умеет читать сам, но WAV надёжнее для noisereduce.
        """

        src = Path(file_path)

        if src.suffix.lower() == ".wav":
            return str(src)

        if shutil.which("ffmpeg") is None:
            log.warning(
                "ffmpeg не найден в PATH, пробую отдать файл в Whisper как есть")
            return str(src)

        out = Path(tempfile.gettempdir()) / f"dirizher_audio_{src.stem}.wav"
        created_files.append(str(out))

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            str(out),
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            log.warning("ffmpeg не смог конвертировать файл: %s",
                        result.stderr[-1000:])
            return str(src)

        return str(out)

    def _denoise(self, file_path: str, created_files: list[str]) -> str:
        """Безопасное шумоподавление.

        Если noisereduce/soundfile не смогли обработать файл — просто продолжаем
        без шумоподавления.
        """

        try:
            import noisereduce as nr
            import soundfile as sf

            data, rate = sf.read(file_path)

            if len(data) == 0:
                return file_path

            reduced = nr.reduce_noise(y=data, sr=rate)

            out = Path(tempfile.gettempdir()) / \
                f"dirizher_clean_{Path(file_path).stem}.wav"
            created_files.append(str(out))

            sf.write(out, reduced, rate)
            return str(out)

        except Exception as e:
            log.warning("Шумоподавление пропущено: %s", e)
            return file_path

    # ── распознавание ────────────────────────────────────────────────────────

    def _whisper_segments(self, file_path: str) -> list["_TimedSegment"]:
        model = self._ensure_model()

        raw_segments, _info = model.transcribe(
            file_path,
            language="ru",
            vad_filter=True,
            beam_size=5,
        )

        result: list[_TimedSegment] = []

        for s in raw_segments:
            text = s.text.strip()
            if text:
                result.append(
                    _TimedSegment(
                        start=s.start,
                        end=s.end,
                        text=text,
                    )
                )

        return result

    # ── опциональная диаризация ──────────────────────────────────────────────

    def _diarize(self, file_path: str):
        diarizer = self._ensure_diarizer()

        if diarizer is None:
            return None

        try:
            annotation = diarizer(file_path)

            turns = []
            for turn, _track, speaker in annotation.itertracks(yield_label=True):
                turns.append((turn.start, turn.end, speaker))

            return turns

        except Exception as e:
            log.warning("Диаризация пропущена: %s", e)
            return None

    @staticmethod
    def _speaker_at(turns, seg) -> str:
        best_speaker = "Speaker_1"
        best_overlap = 0.0

        for start, end, speaker in turns:
            overlap = max(0.0, min(seg.end, end) - max(seg.start, start))

            if overlap > best_overlap:
                best_speaker = speaker
                best_overlap = overlap

        return best_speaker


class _TimedSegment(Segment):
    def __init__(
        self,
        start: float,
        end: float,
        text: str,
        speaker: str = "Speaker_1",
    ) -> None:
        super().__init__(speaker=speaker, text=text)
        self.start = start
        self.end = end
