"""Локальный пайплайн распознавания речи (backend=local).

Поток: Telegram .oga/.ogg/.mp4 → ffmpeg → wav 16kHz mono → noisereduce →
faster-whisper. Диаризация через pyannote опциональна (нужен HF-токен).

Тяжёлые зависимости (faster-whisper, noisereduce, soundfile, pyannote)
импортируются лениво и нужны только при DIRIZHER_AUDIO__BACKEND=local.
Для облачного распознавания см. groq_transcriber.GroqWhisperTranscriber.
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

    def __init__(self, cfg: AudioSettings, speaker_registry=None, embedder=None) -> None:
        self._cfg = cfg
        self._model = None
        self._diarizer = None
        self._registry = speaker_registry  # SpeakerRegistry | None — авто-имена по голосу
        self._embedder = embedder  # EmbeddingExtractor | None

    # ── публичный API ────────────────────────────────────────────────────────
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
                # Сырые метки SPEAKER_00/01 → реальные имена по голосу (если знаем),
                # иначе Speaker_1/2. На встрече это даёт «Имя: реплика» для LLM/саммари.
                label_map = self._resolve_speakers(clean_path, diar)
                for seg in segments:
                    seg.speaker = label_map.get(seg.speaker, seg.speaker)
                text = self._join_consecutive(segments)
            elif self._can_identify():
                # Нет диаризации (нет pyannote/HF-токена), но голоса команды
                # зарегистрированы → опознаём каждый сегмент по голосу напрямую.
                self._identify_segments(clean_path, segments)
                text = self._join_consecutive(segments)
            else:
                # Один голос (личное ГС/кружок) — метки спикеров не нужны.
                text = " ".join(s.text for s in segments).strip()
            return TranscriptResult(text=text, segments=segments, is_mock=False)
        finally:
            for path in created_files:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass

    # ── ленивая инициализация моделей ────────────────────────────────────────
    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            device, compute = self._pick_device()
            log.info("Загружаю Whisper-модель %s (%s/%s)…", self._cfg.whisper_model, device, compute)
            self._model = WhisperModel(self._cfg.whisper_model, device=device, compute_type=compute)
        return self._model

    @staticmethod
    def _pick_device() -> tuple[str, str]:
        """CUDA-GPU если есть (float16), иначе CPU (int8). torch — мягкая зависимость."""
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda", "float16"
        except Exception:  # noqa: BLE001
            pass
        return "cpu", "int8"

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
                self._diarizer = None
        return self._diarizer

    # ── подготовка аудио ─────────────────────────────────────────────────────
    def _to_wav(self, file_path: str, created_files: list[str]) -> str:
        """Конвертирует Telegram voice/video_note в WAV 16kHz mono.

        voice приходит как .oga/.ogg (Opus), video_note — как .mp4. soundfile не
        читает Opus, поэтому без конвертации шумоподавление невозможно. Если ffmpeg
        в PATH нет — отдаём файл whisper'у как есть (он умеет читать сам).
        """
        src = Path(file_path)
        if src.suffix.lower() == ".wav":
            return str(src)
        if shutil.which("ffmpeg") is None:
            log.warning("ffmpeg не найден в PATH — отдаю файл whisper как есть")
            return str(src)

        out = Path(tempfile.gettempdir()) / f"dirizher_audio_{src.stem}.wav"
        created_files.append(str(out))
        cmd = ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", "-vn", str(out)]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            log.warning("ffmpeg не смог конвертировать файл: %s", result.stderr[-1000:])
            return str(src)
        return str(out)

    def _denoise(self, file_path: str, created_files: list[str]) -> str:
        """Шумоподавление перед транскрипцией (+качество на плохих записях)."""
        try:
            import noisereduce as nr
            import soundfile as sf

            data, rate = sf.read(file_path)
            if len(data) == 0:
                return file_path
            reduced = nr.reduce_noise(y=data, sr=rate)
            out = Path(tempfile.gettempdir()) / f"dirizher_clean_{Path(file_path).stem}.wav"
            created_files.append(str(out))
            sf.write(out, reduced, rate)
            return str(out)
        except Exception as e:  # noqa: BLE001
            log.warning("Шумоподавление пропущено (%s)", e)
            return file_path

    # ── распознавание ────────────────────────────────────────────────────────
    def _whisper_segments(self, file_path: str) -> list["_TimedSegment"]:
        model = self._ensure_model()
        raw_segments, _info = model.transcribe(file_path, language="ru", vad_filter=True, beam_size=5)
        result: list[_TimedSegment] = []
        for s in raw_segments:
            text = s.text.strip()
            if text:
                result.append(_TimedSegment(start=s.start, end=s.end, text=text))
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
        except Exception as e:  # noqa: BLE001
            log.warning("Диаризация пропущена (%s)", e)
            return None

    @staticmethod
    def _speaker_at(turns, seg) -> str:
        """Спикер с максимальным временным перекрытием для сегмента."""
        best, best_ov = "Speaker_1", 0.0
        for start, end, speaker in turns:
            ov = max(0.0, min(seg.end, end) - max(seg.start, start))
            if ov > best_ov:
                best, best_ov = speaker, ov
        return best

    def _resolve_speakers(self, wav_path: str, turns: list) -> dict[str, str]:
        """Сырые метки pyannote → имена. По голосу (эмбеддинг+реестр) или Speaker_N.

        Порядок появления сохраняем, чтобы нумерация была стабильной и читаемой.
        """
        raw_order: list[str] = []
        for _start, _end, spk in turns:
            if spk not in raw_order:
                raw_order.append(spk)

        # Эмбеддинги по спикерам (если есть эмбеддер и токен) → имя из реестра.
        embeddings: dict[str, list[float]] = {}
        if self._embedder is not None and self._registry is not None:
            try:
                embeddings = self._embedder.embed_turns(wav_path, turns)
            except Exception as e:  # noqa: BLE001
                log.warning("Эмбеддинги спикеров недоступны (%s)", e)

        label_map: dict[str, str] = {}
        used_names: set[str] = set()
        anon = 0
        for raw in raw_order:
            name = None
            emb = embeddings.get(raw)
            if emb is not None:
                cand = self._registry.identify(emb)
                if cand and cand not in used_names:  # один человек — один спикер
                    name = cand
            if name is None:
                anon += 1
                name = f"Speaker_{anon}"
            used_names.add(name)
            label_map[raw] = name
        return label_map

    # ── идентификация без диаризации (closed-set по реестру голосов) ──────────
    def _can_identify(self) -> bool:
        """Можно опознавать спикеров напрямую: есть эмбеддер и зарегистрированные голоса."""
        return bool(self._embedder is not None and self._registry is not None and len(self._registry))

    def _identify_segments(self, wav_path: str, segments: list) -> None:
        """Разметить сегменты именами говорящих через кластеризацию голосов.

        MFCC на коротких сегментах слишком шумный, чтобы решать по каждому
        отдельно (то расщепляет один голос, то склеивает два). Поэтому:
        1) кластеризуем эмбеддинги сегментов по похожести голоса
           (агломеративно, порог = по близости реестра) — это и есть «кто-когда»;
        2) реестр голосов используем лишь чтобы НАЗВАТЬ кластеры (средний
           эмбеддинг кластера → ближайший зарегистрированный → имя).
        Так один голос = один кластер (а если распался — оба назовутся одним
        именем), двое разных = два кластера. Незаписанные куски наследуют соседа.
        """
        turns = [(s.start, s.end, str(i)) for i, s in enumerate(segments)]
        try:
            embs = self._embedder.embed_turns(wav_path, turns)
        except Exception as e:  # noqa: BLE001
            log.warning("Идентификация спикеров пропущена (%s)", e)
            return
        idxs = [i for i in range(len(segments)) if str(i) in embs]
        if not idxs:
            return
        vecs = [embs[str(i)] for i in idxs]
        labels = self._cluster(vecs)

        # Назвать каждый кластер по среднему эмбеддингу (или Speaker_N, если чужой).
        cluster_name: dict[int, str] = {}
        anon = 0
        for cid in sorted(set(labels)):
            members = [vecs[j] for j, lab in enumerate(labels) if lab == cid]
            name = self._registry.identify(self._mean_vec(members))
            if name is None:
                anon += 1
                name = f"Speaker_{anon}"
            cluster_name[cid] = name

        seg_name = {i: cluster_name[labels[j]] for j, i in enumerate(idxs)}
        last = next(iter(cluster_name.values()))
        for i, s in enumerate(segments):
            s.speaker = seg_name.get(i, last)
            last = s.speaker

    def _cluster(self, vecs: list[list[float]]) -> list[int]:
        """Агломеративная кластеризация эмбеддингов по косинусу.

        Порог среза берём от близости реестра: голоса ближе порога считаем одним
        говорящим. Если все сегменты похожи — выйдет один кластер.
        """
        if len(vecs) == 1:
            return [0]
        import numpy as np
        from scipy.cluster.hierarchy import fcluster, linkage
        from scipy.spatial.distance import pdist

        dist = pdist(np.asarray(vecs, dtype=float), metric="cosine")
        if dist.size == 0:
            return [0] * len(vecs)
        z = linkage(dist, method="average")
        cut = max(0.05, 1.0 - self._registry.threshold)  # косинус-дистанция среза
        return [int(c) for c in fcluster(z, t=cut, criterion="distance")]

    @staticmethod
    def _mean_vec(vectors: list[list[float]]) -> list[float]:
        n = len(vectors)
        length = len(vectors[0])
        return [sum(v[k] for v in vectors) / n for k in range(length)]

    @staticmethod
    def _join_consecutive(segments: list) -> str:
        """Склеить реплики, группируя подряд идущие по спикеру: «Имя: реплика»."""
        lines: list[str] = []
        cur: str | None = None
        buf: list[str] = []
        for s in segments:
            if s.speaker != cur:
                if buf:
                    lines.append(f"{cur}: {' '.join(buf)}")
                cur, buf = s.speaker, [s.text]
            else:
                buf.append(s.text)
        if buf:
            lines.append(f"{cur}: {' '.join(buf)}")
        return "\n".join(lines).strip()


class _TimedSegment(Segment):
    """Сегмент с таймкодами (наследует speaker/text)."""

    def __init__(self, start: float, end: float, text: str, speaker: str = "Speaker_1") -> None:
        super().__init__(speaker=speaker, text=text)
        self.start = start
        self.end = end
