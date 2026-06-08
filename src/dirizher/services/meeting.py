"""Обработка онлайн-встречи: транскрипт → саммари → задачи.

Принимает результат распознавания (диаризованный транскрипт), формирует
человекочитаемое саммари и извлекает задачи тем же конвейером, что и чат
(источник = встреча). Спикеры мэпятся на участников через реестр/команду.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..audio.transcriber import TranscriptResult
from ..domain.enums import TaskSource
from ..domain.models import SourceRef
from ..logging_setup import get_logger
from ..services.task_service import ProcessedTask, TaskService

log = get_logger("dirizher.meeting")

_TRIGGER_HINTS = ["сделать", "сделай", "подготов", "нужно", "надо", "дедлайн", "до ", "к "]


@dataclass
class MeetingResult:
    summary: str
    processed: list[ProcessedTask]
    transcript_text: str


class MeetingService:
    def __init__(self, service: TaskService) -> None:
        self.service = service

    def _label_speakers(self, transcript: TranscriptResult) -> str:
        """Собрать текст встречи, заменив Speaker_N на имена, где возможно."""
        lines: list[str] = []
        mapping: dict[str, str] = {}
        for seg in transcript.segments:
            who = mapping.get(seg.speaker)
            if who is None:
                member = self.service.team.resolve(seg.speaker)
                if member:
                    who = f"@{member.username}" if member.username else (member.full_name or seg.speaker)
                else:
                    who = seg.speaker
                mapping[seg.speaker] = who
            lines.append(f"{who}: {seg.text}")
        return "\n".join(lines) if lines else transcript.text

    @staticmethod
    def _summarize(text: str, max_points: int = 6) -> str:
        """Краткое саммари: договорённости/задачи с указанием, кто их озвучил.

        На вход — размеченный текст («@user: реплика» построчно). Для каждой
        строки запоминаем спикера и тащим его в пункты саммари, чтобы было видно,
        КТО что сказал. Строки без явного спикера (плоский текст) — как есть.
        """
        import re

        points: list[str] = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            who, sep, body = line.partition(": ")
            if not sep:  # нет префикса спикера — вся строка как тело
                who, body = "", line
            for sent in re.split(r"(?<=[.!?])\s+", body):
                sent = sent.strip()
                if sent and any(h in sent.lower() for h in _TRIGGER_HINTS):
                    points.append(f"{who}: {sent}" if who else sent)
                    if len(points) >= max_points:
                        break
            if len(points) >= max_points:
                break
        if not points:
            return "Ключевых договорённостей не выделено."
        return "\n".join(f"• {p}" for p in points)

    async def process(
        self, transcript: TranscriptResult, *, chat_id: int | None = None, today: date | None = None
    ) -> MeetingResult:
        text = self._label_speakers(transcript)
        summary = self._summarize(text)
        source = SourceRef(source=TaskSource.meeting, chat_id=chat_id, excerpt=text[:200])
        processed = await self.service.ingest(text, source, today=today)
        log.info("Встреча обработана: задач=%d", len(processed))
        return MeetingResult(summary=summary, processed=processed, transcript_text=text)
