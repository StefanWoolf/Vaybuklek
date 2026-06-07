"""Реестр голосов: голосовой отпечаток (speaker embedding) → участник.

На первой встрече бот просит представиться и сохраняет эмбеддинг голоса.
Дальше анонимный Speaker_1 мэпится на «Алексея» по близости эмбеддингов —
без обращения к LLM (механика из отчёта, раздел 3.3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..logging_setup import get_logger

log = get_logger("dirizher.audio.speakers")


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class VoicePrint:
    name: str
    embedding: list[float]


class SpeakerRegistry:
    """Персистентный (JSON) реестр голосовых отпечатков."""

    def __init__(self, path: str = "./.data/voiceprints.json", threshold: float = 0.75) -> None:
        self._path = Path(path)
        self._threshold = threshold
        self._prints: list[VoicePrint] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._prints = [VoicePrint(**d) for d in data]

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([vp.__dict__ for vp in self._prints], ensure_ascii=False),
            encoding="utf-8",
        )

    def enroll(self, name: str, embedding: list[float]) -> None:
        self._prints = [vp for vp in self._prints if vp.name != name]
        self._prints.append(VoicePrint(name=name, embedding=embedding))
        self._save()
        log.info("Голос зарегистрирован: %s", name)

    def identify(self, embedding: list[float]) -> str | None:
        """Вернуть имя ближайшего известного голоса или None."""
        best_name, best_score = None, 0.0
        for vp in self._prints:
            score = _cosine(embedding, vp.embedding)
            if score >= self._threshold and score > best_score:
                best_name, best_score = vp.name, score
        return best_name
