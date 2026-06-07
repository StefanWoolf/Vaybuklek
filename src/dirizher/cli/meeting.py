"""CLI обработки записи встречи: аудио/транскрипт → саммари → задачи на доску.

Запуск:
  python -m dirizher.cli.meeting путь/к/записи.wav      # боевой пайплайн (audio enabled)
  python -m dirizher.cli.meeting путь/к/транскрипту.txt # демо без аудио-зависимостей

Формат .txt: строки вида «Имя: реплика» (по одной на строку).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ..audio.transcriber import Segment, TranscriptResult
from ..container import AppContainer
from ..domain.models import TeamMember
from ..logging_setup import setup_logging
from ..services.task_service import Outcome

DEMO_TEAM = [
    TeamMember(user_id=1, username="maxim", full_name="Максим", aliases=["Максим", "Макс"]),
    TeamMember(user_id=2, username="dasha", full_name="Дарья", aliases=["Даша", "Дарья"]),
    TeamMember(user_id=3, username="andrey", full_name="Андрей", aliases=["Андрей"]),
]


def _transcript_from_txt(path: Path) -> TranscriptResult:
    segments: list[Segment] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            who, text = line.split(":", 1)
            segments.append(Segment(speaker=who.strip(), text=text.strip()))
        else:
            segments.append(Segment(speaker="Speaker_1", text=line))
    return TranscriptResult(text=" ".join(s.text for s in segments), segments=segments)


async def main() -> None:
    setup_logging("INFO")
    if len(sys.argv) < 2:
        print("Использование: python -m dirizher.cli.meeting <файл.wav|файл.txt>")
        return
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Файл не найден: {path}")
        return

    c = AppContainer()
    for m in DEMO_TEAM:
        c.team.register(m)

    if path.suffix.lower() == ".txt":
        transcript = _transcript_from_txt(path)
    else:
        transcript = await c.transcriber.transcribe(str(path))
        if transcript.is_mock or not transcript.text.strip():
            print(
                "🎙️ Аудио-распознавание выключено (DIRIZHER_AUDIO__ENABLED=false) "
                "или речь не распознана.\nДля демо передайте .txt-транскрипт."
            )
            await c.aclose()
            return

    result = await c.meeting.process(transcript, chat_id=1000)
    print("\n📝 САММАРИ ВСТРЕЧИ\n" + result.summary)
    print("\n📋 ИЗВЛЕЧЁННЫЕ ЗАДАЧИ")
    if not result.processed:
        print("  (задач не найдено)")
    for p in result.processed:
        tag = {Outcome.new: "🆕", Outcome.duplicate: "♻️", Outcome.low_confidence: "🤔"}[p.outcome]
        print(f"  {tag} {p.task.title} — {p.task.assignee or '—'} — до {p.task.deadline or '—'} "
              f"(conf={p.task.confidence})")
        if p.outcome is Outcome.new:
            created = await c.service.create_on_board(p.task)
            print(f"      → создано на доске: {created.board_card_id}")
    await c.aclose()


if __name__ == "__main__":
    asyncio.run(main())
