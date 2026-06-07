"""Векторная память и дедупликация задач.

Боевой режим — ChromaDB (семантический поиск): «сделать API» и «разработать
эндпоинт» распознаются как одна задача. Если chromadb недоступен/не настроен,
используется лексический fallback (косинус по токенам) — он ловит явные
near-дубликаты и сохраняет работоспособность демо без зависимостей.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from ..logging_setup import get_logger

log = get_logger("dirizher.memory")

_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
# Шумовые слова, не несущие смысла для сравнения задач
_STOP = {
    "и", "в", "на", "к", "до", "по", "с", "у", "о", "за", "из", "для",
    "это", "the", "a", "to", "of", "сделать", "сделай", "нужно", "надо",
}


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOP]


@dataclass
class DuplicateMatch:
    task_id: str
    score: float


class _LexicalBackend:
    """Fallback: косинусная близость по мешку слов."""

    name = "lexical"

    def __init__(self) -> None:
        self._docs: dict[str, Counter] = {}

    @staticmethod
    def _vec(text: str) -> Counter:
        return Counter(_tokens(text))

    @staticmethod
    def _cosine(a: Counter, b: Counter) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def add(self, task_id: str, text: str) -> None:
        self._docs[task_id] = self._vec(text)

    def remove(self, task_id: str) -> None:
        self._docs.pop(task_id, None)

    def query(self, text: str, threshold: float) -> DuplicateMatch | None:
        qv = self._vec(text)
        best: DuplicateMatch | None = None
        for tid, dv in self._docs.items():
            score = self._cosine(qv, dv)
            if score >= threshold and (best is None or score > best.score):
                best = DuplicateMatch(tid, round(score, 3))
        return best


class _ChromaBackend:
    """Семантический бэкенд на ChromaDB."""

    name = "chroma"

    def __init__(self, path: str) -> None:
        import chromadb  # ленивый импорт

        self._client = chromadb.PersistentClient(path=path)
        self._col = self._client.get_or_create_collection(
            "dirizher_tasks", metadata={"hnsw:space": "cosine"}
        )

    def add(self, task_id: str, text: str) -> None:
        self._col.upsert(ids=[task_id], documents=[text])

    def remove(self, task_id: str) -> None:
        try:
            self._col.delete(ids=[task_id])
        except Exception:  # noqa: BLE001
            pass

    def query(self, text: str, threshold: float) -> DuplicateMatch | None:
        if self._col.count() == 0:
            return None
        res = self._col.query(query_texts=[text], n_results=1)
        ids = res.get("ids", [[]])[0]
        dists = res.get("distances", [[]])[0]
        if not ids:
            return None
        score = 1.0 - float(dists[0])  # cosine distance -> similarity
        if score >= threshold:
            return DuplicateMatch(ids[0], round(score, 3))
        return None


class TaskMemory:
    """Фасад дедупликации. Прозрачно выбирает бэкенд."""

    def __init__(self, chroma_path: str, threshold: float, *, backend: str = "lexical") -> None:
        self._threshold = threshold
        self._backend = self._make_backend(chroma_path, backend)
        log.info("Память дедупликации: backend=%s, threshold=%.2f", self.backend_name, threshold)

    @staticmethod
    def _make_backend(path: str, backend: str):
        if backend == "chroma":
            try:
                return _ChromaBackend(path)
            except Exception as e:  # noqa: BLE001
                log.warning("ChromaDB недоступен (%s) — лексический fallback", e)
        return _LexicalBackend()

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def find_duplicate(self, text: str) -> DuplicateMatch | None:
        return self._backend.query(text, self._threshold)

    def remember(self, task_id: str, text: str) -> None:
        self._backend.add(task_id, text)

    def forget(self, task_id: str) -> None:
        self._backend.remove(task_id)
