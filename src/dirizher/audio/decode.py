"""Декодирование аудио в моно float32 @16 кГц — без бинарника ffmpeg.

Telegram присылает голосовые как .oga (Opus), кружки как .mp4. libsndfile
(soundfile) не читает Opus, а ffmpeg в PATH может не быть. Поэтому для всего,
кроме WAV, используем PyAV (`av`) — он несёт в себе библиотеки ffmpeg и
декодирует Opus/AAC/MP4 без внешних зависимостей.

Возвращаем нормализованный сигнал (моно, 16 кГц) — ровно то, что нужно и для
голосовых эмбеддингов, и для распознавания.
"""

from __future__ import annotations

import numpy as np

TARGET_SR = 16000


def _resample(samples: np.ndarray, src_sr: int, dst_sr: int = TARGET_SR) -> np.ndarray:
    if src_sr == dst_sr or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    # Линейная интерполяция — достаточно для речи; без зависимости от scipy здесь.
    duration = samples.shape[0] / float(src_sr)
    dst_n = int(round(duration * dst_sr))
    if dst_n <= 0:
        return np.zeros(0, dtype=np.float32)
    src_idx = np.linspace(0.0, samples.shape[0] - 1, dst_n)
    base = np.arange(samples.shape[0])
    return np.interp(src_idx, base, samples).astype(np.float32)


def decode_mono16k(path: str) -> tuple[np.ndarray, int]:
    """Прочитать аудиофайл как моно float32 @16 кГц.

    WAV читаем через soundfile, всё остальное (Opus/.oga/.mp4/...) — через PyAV.
    """
    if str(path).lower().endswith(".wav"):
        try:
            import soundfile as sf

            data, sr = sf.read(path, dtype="float32", always_2d=False)
            data = np.asarray(data, dtype=np.float32)
            if data.ndim > 1:
                data = data.mean(axis=1)
            return _resample(data, sr), TARGET_SR
        except Exception:  # noqa: BLE001
            pass  # упадём в PyAV-ветку ниже

    import av  # PyAV: декодирование без бинарника ffmpeg

    chunks: list[np.ndarray] = []
    src_sr = TARGET_SR
    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            return np.zeros(0, dtype=np.float32), TARGET_SR
        for frame in container.decode(stream):
            arr = frame.to_ndarray()  # (channels, n) или (1, n) для упакованных
            if arr.ndim > 1:
                arr = arr.mean(axis=0)
            chunks.append(arr.astype(np.float32))
            if frame.sample_rate:
                src_sr = frame.sample_rate
    if not chunks:
        return np.zeros(0, dtype=np.float32), TARGET_SR
    samples = np.concatenate(chunks)
    # Целочисленные форматы (int16/int32) → нормируем в [-1, 1].
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 1.5:
        samples = samples / 32768.0
    return _resample(samples, src_sr), TARGET_SR
