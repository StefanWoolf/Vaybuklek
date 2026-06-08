"""Извлечение голосовых эмбеддингов (speaker embedding).

Эмбеддинг — это числовой «отпечаток» голоса. Его сравнивают по косинусной
близости с зарегистрированными (см. SpeakerRegistry), чтобы анонимного
Speaker_1 заменить реальным именем без обращения к LLM.

Два бэкенда:
- SignalEmbedder (по умолчанию) — MFCC-отпечаток на numpy/scipy. Работает офлайн,
  без HF-токена и без бинарника ffmpeg (декодирование через PyAV). Точности
  хватает для закрытого множества из нескольких разных голосов команды.
- PyannoteEmbedder — нейросетевые эмбеддинги pyannote (точнее, но нужен HF-токен
  и backend=local). Включается, когда задан DIRIZHER_AUDIO__HF_TOKEN.

Тяжёлые зависимости импортируются лениво.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..config import AudioSettings
from ..logging_setup import get_logger

log = get_logger("dirizher.audio.embeddings")


@runtime_checkable
class EmbeddingExtractor(Protocol):
    def embed_file(self, wav_path: str) -> list[float]: ...
    def embed_turns(self, wav_path: str, turns: list[tuple[float, float, str]]) -> dict[str, list[float]]: ...


class PyannoteEmbedder:
    name = "pyannote"

    def __init__(self, cfg: AudioSettings) -> None:
        self._cfg = cfg
        self._inference = None

    def _ensure(self):
        if self._inference is None:
            from pyannote.audio import Inference, Model

            log.info("Загружаю модель эмбеддингов %s…", self._cfg.embedding_model)
            model = Model.from_pretrained(self._cfg.embedding_model, use_auth_token=self._cfg.hf_token)
            self._inference = Inference(model, window="whole")
        return self._inference

    def embed_file(self, wav_path: str) -> list[float]:
        """Эмбеддинг всей записи (для регистрации голоса одного человека)."""
        emb = self._ensure()(wav_path)
        return _to_list(emb)

    def embed_turns(
        self, wav_path: str, turns: list[tuple[float, float, str]]
    ) -> dict[str, list[float]]:
        """Усреднённый эмбеддинг по сегментам каждого спикера диаризации."""
        from pyannote.core import Segment

        inf = self._ensure()
        by_speaker: dict[str, list[list[float]]] = {}
        for start, end, speaker in turns:
            if end - start < 0.4:  # слишком короткие куски пропускаем
                continue
            try:
                emb = inf.crop(wav_path, Segment(start, end))
            except Exception:  # noqa: BLE001
                continue
            by_speaker.setdefault(speaker, []).append(_to_list(emb))
        return {spk: _mean(vecs) for spk, vecs in by_speaker.items() if vecs}


class SignalEmbedder:
    """Голосовой отпечаток на классических MFCC — без сети и HF-токена.

    Считаем MFCC по кадрам и берём их среднее и СКО по времени: тембр голоса
    отражается в среднем, динамика — в разбросе. Получаем компактный вектор,
    который сравниваем по косинусу. Декодирование — через PyAV (любой Opus/mp4),
    поэтому ffmpeg в PATH не нужен.
    """

    name = "signal"

    def __init__(self, sr: int = 16000, n_mfcc: int = 13, n_mels: int = 26) -> None:
        self._sr = sr
        self._n_mfcc = n_mfcc
        self._n_mels = n_mels
        self._fb = None  # мел-фильтрбанк (ленивая инициализация)

    # ── публичный API эмбеддера ──────────────────────────────────────────────
    def embed_file(self, wav_path: str) -> list[float]:
        from .decode import decode_mono16k

        samples, sr = decode_mono16k(wav_path)
        return self._embed_samples(samples, sr)

    def embed_turns(
        self, wav_path: str, turns: list[tuple[float, float, str]]
    ) -> dict[str, list[float]]:
        from .decode import decode_mono16k

        samples, sr = decode_mono16k(wav_path)
        by_speaker: dict[str, list[list[float]]] = {}
        for start, end, speaker in turns:
            if end - start < 0.4:  # слишком короткие куски ненадёжны
                continue
            a, b = int(start * sr), int(end * sr)
            vec = self._embed_samples(samples[a:b], sr)
            if vec:
                by_speaker.setdefault(speaker, []).append(vec)
        return {spk: _mean(vecs) for spk, vecs in by_speaker.items() if vecs}

    # ── вычисление признаков ─────────────────────────────────────────────────
    def _embed_samples(self, samples, sr: int) -> list[float]:
        import numpy as np

        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if x.size < int(0.3 * sr):  # меньше ~0.3 c — слишком мало для отпечатка
            return []
        mfcc = self._mfcc(x, sr)  # (n_frames, n_mfcc)
        if mfcc.shape[0] < 3:
            return []
        # c0 (энергия) сильно зависит от громкости/микрофона и одинаков у всех —
        # для тембра берём средние c1.. (форма спектра), плюс СКО всех коэф-тов
        # (динамика речи). Затем стандартизуем вектор: косинус начинает измерять
        # «узор» голоса, а не общий положительный сдвиг → голоса разделяются.
        vec = np.concatenate([mfcc[:, 1:].mean(axis=0), mfcc.std(axis=0)])
        vec = vec - vec.mean()
        std = float(vec.std())
        if std:
            vec = vec / std
        norm = float(np.linalg.norm(vec))
        return (vec / norm).tolist() if norm else vec.tolist()

    def _mfcc(self, x, sr: int):
        import numpy as np
        from scipy.fftpack import dct

        # Преэмфазис — поднимаем высокие частоты (стандартный шаг для речи).
        x = np.append(x[0], x[1:] - 0.97 * x[:-1])
        frame_len = int(0.025 * sr)  # 25 мс
        frame_step = int(0.010 * sr)  # 10 мс
        if x.shape[0] < frame_len:
            return np.zeros((0, self._n_mfcc), dtype=np.float32)
        n_frames = 1 + (x.shape[0] - frame_len) // frame_step
        idx = np.arange(frame_len)[None, :] + frame_step * np.arange(n_frames)[:, None]
        frames = x[idx] * np.hamming(frame_len)
        n_fft = 512
        mag = np.abs(np.fft.rfft(frames, n=n_fft))
        pow_spec = (mag ** 2) / n_fft
        fb = self._filterbank(sr, n_fft)
        mel = np.maximum(pow_spec @ fb.T, 1e-10)
        log_mel = np.log(mel)
        feats = dct(log_mel, type=2, axis=1, norm="ortho")[:, : self._n_mfcc]
        return feats.astype(np.float32)

    def _filterbank(self, sr: int, n_fft: int):
        import numpy as np

        if self._fb is not None:
            return self._fb
        low, high = 0.0, sr / 2.0
        to_mel = lambda f: 2595.0 * np.log10(1.0 + f / 700.0)  # noqa: E731
        from_mel = lambda m: 700.0 * (10.0 ** (m / 2595.0) - 1.0)  # noqa: E731
        mel_pts = np.linspace(to_mel(low), to_mel(high), self._n_mels + 2)
        hz_pts = from_mel(mel_pts)
        bins = np.floor((n_fft + 1) * hz_pts / sr).astype(int)
        fb = np.zeros((self._n_mels, n_fft // 2 + 1), dtype=np.float32)
        for m in range(1, self._n_mels + 1):
            left, center, right = bins[m - 1], bins[m], bins[m + 1]
            for k in range(left, center):
                if center != left:
                    fb[m - 1, k] = (k - left) / (center - left)
            for k in range(center, right):
                if right != center:
                    fb[m - 1, k] = (right - k) / (right - center)
        self._fb = fb
        return fb


def _to_list(emb) -> list[float]:
    try:
        import numpy as np

        return np.asarray(emb).reshape(-1).astype(float).tolist()
    except Exception:  # noqa: BLE001
        return list(emb)


def _mean(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    if n == 1:
        return vectors[0]
    length = len(vectors[0])
    return [sum(v[i] for v in vectors) / n for i in range(length)]


def build_embedder(cfg: AudioSettings) -> EmbeddingExtractor | None:
    """Эмбеддер для авто-имён по голосу.

    - HF-токен задан (и backend=local) → PyannoteEmbedder (макс. точность).
    - иначе → SignalEmbedder (офлайн, без токена) — работает на любом бэкенде.
    - audio выключен → None (фича не нужна).
    """
    if cfg.is_mock:
        return None
    if cfg.backend == "local" and cfg.hf_token:
        log.info("Голосовые эмбеддинги: pyannote (%s)", cfg.embedding_model)
        return PyannoteEmbedder(cfg)
    log.info("Голосовые эмбеддинги: signal (MFCC, офлайн, без HF-токена)")
    return SignalEmbedder(sr=cfg.meeting_samplerate)
