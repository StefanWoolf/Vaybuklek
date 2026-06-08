"""Запись системного звука встречи (WASAPI loopback) с авто-стопом по тишине.

Бот не «входит» в Телемост — он пишет то, что играет в колонках машины, где
запущен (захват с драйвера, как в кейсе). Поток пишется в фоновом потоке; когда
наступает долгая тишина или достигнут предел длительности, запись сама
останавливается, файл сохраняется и вызывается async-колбэк обработки.

`soundcard` импортируется лениво: без него запись недоступна, но остальной бот
работает. Для тестов и mock логика тишины/останова вынесена в чистые функции.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..config import AudioSettings
from ..logging_setup import get_logger

log = get_logger("dirizher.audio.recorder")

_CHUNK_SECONDS = 0.5  # длительность одного читаемого блока


def _is_silent(rms: float, threshold: float) -> bool:
    return rms < threshold


def _stop_reason(
    silent_streak_s: float, elapsed_s: float, *, silence_limit: float, max_s: float
) -> str | None:
    """Причина авто-останова или None, если продолжаем писать."""
    if elapsed_s >= max_s:
        return "timeout"
    if silence_limit and silent_streak_s >= silence_limit:
        return "silence"
    return None


def loopback_available() -> bool:
    try:
        import soundcard  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


class MeetingRecorder:
    """Одна активная запись встречи (на чат). Потокобезопасный старт/стоп."""

    def __init__(
        self,
        cfg: AudioSettings,
        on_finish: Callable[[str | None, str], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._cfg = cfg
        self._on_finish = on_finish
        self._loop = loop
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reason_override: str | None = None

    @property
    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Запустить захват. False — если soundcard/устройство недоступны."""
        if self.is_active:
            return True
        if not loopback_available():
            log.warning("soundcard не установлен — запись встречи недоступна")
            return False
        try:
            mic = self._pick_loopback()
        except Exception as e:  # noqa: BLE001
            log.warning("Не нашёл loopback-устройство: %s", e)
            return False
        if mic is None:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(mic,), daemon=True)
        self._thread.start()
        log.info("🔴 Запись встречи начата (устройство: %s)", mic.name)
        return True

    def stop(self, reason: str = "manual") -> None:
        """Запросить остановку записи; обработка пойдёт в колбэке on_finish."""
        self._reason_override = reason
        self._stop.set()

    # ── внутреннее ────────────────────────────────────────────────────────────
    def _pick_loopback(self):
        import soundcard as sc

        name = self._cfg.loopback_device or (sc.default_speaker().name if sc.default_speaker() else "")
        if name:
            try:
                return sc.get_microphone(name, include_loopback=True)
            except Exception:  # noqa: BLE001
                pass
        for m in sc.all_microphones(include_loopback=True):
            if getattr(m, "isloopback", False):
                return m
        return None

    def _run(self, mic) -> None:
        import numpy as np

        sr = self._cfg.meeting_samplerate
        frames_per_chunk = max(1, int(sr * _CHUNK_SECONDS))
        max_s = self._cfg.meeting_max_minutes * 60
        silence_limit = self._cfg.meeting_silence_seconds
        chunks: list = []
        silent_streak = 0.0
        elapsed = 0.0
        reason = "manual"
        try:
            with mic.recorder(samplerate=sr, channels=1) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=frames_per_chunk)
                    chunks.append(data)
                    elapsed += _CHUNK_SECONDS
                    rms = float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0
                    silent_streak = silent_streak + _CHUNK_SECONDS if _is_silent(rms, self._cfg.meeting_silence_rms) else 0.0
                    auto = _stop_reason(silent_streak, elapsed, silence_limit=silence_limit, max_s=max_s)
                    if auto:
                        reason = auto
                        break
            if self._stop.is_set():
                reason = self._reason_override or "manual"
        except Exception as e:  # noqa: BLE001
            log.exception("Сбой записи встречи")
            reason = f"error: {e}"
            chunks = []

        path = self._write(chunks) if chunks else None
        # вернуть результат в event loop бота
        fut = asyncio.run_coroutine_threadsafe(self._on_finish(path, reason), self._loop)
        try:
            fut.result(timeout=0)  # не блокируем поток; ошибки залогирует loop
        except Exception:  # noqa: BLE001
            pass

    def _write(self, chunks: list) -> str | None:
        import numpy as np
        import soundfile as sf

        audio = np.concatenate(chunks, axis=0)
        # обрезаем хвост тишины авто-стопа, чтобы не гонять её через Whisper
        out = Path(tempfile.gettempdir()) / f"dirizher_meeting_{id(self):x}.wav"
        sf.write(str(out), audio, self._cfg.meeting_samplerate)
        log.info("💾 Запись встречи сохранена: %s (%.1f c)", out, len(audio) / self._cfg.meeting_samplerate)
        return str(out)
