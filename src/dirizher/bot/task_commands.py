"""Команды над уже существующими задачами прямо из чата.

Распознаёт сообщения вида:
  «Закрой задачу 8a51c61e-…»            → перевести в «Готово»
  «@danya закрыл задачу по презентации» → найти по ключевым словам и закрыть
  «возьми в работу <id>»                → колонка «В работе»
  «удали задачу <id>»                   → удалить карточку

Работает ДО извлечения новых задач, чтобы не плодить дубли.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape as esc

from aiogram.types import Message

from ..container import AppContainer
from ..domain.enums import TaskStatus
from ..logging_setup import get_logger

log = get_logger("dirizher.bot.taskcmd")

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_TOKEN = re.compile(r"[а-яёa-z0-9]{4,}", re.IGNORECASE)

# Глаголы-маркеры действий над существующей задачей
_DONE = ("закрой", "закрыл", "закрыто", "выполнена", "выполнено", "выполнил", "готова",
         "готово", "заверши", "завершил", "сделана", "done", "доделал")
_START = ("в работу", "в работе", "возьми в работу", "взял в работу", "начал", "приступил")
_DELETE = ("удали", "удалить", "снеси", "убери задач", "удали задач")
_REF = ("задач", "таск", "карточк")


@dataclass
class TaskCommand:
    action: str  # done | start | delete
    card_id: str | None
    keywords: list[str]


def detect(text: str) -> TaskCommand | None:
    low = text.lower()
    uuid_m = _UUID.search(text)
    card_id = uuid_m.group(0) if uuid_m else None

    if any(k in low for k in _DELETE) and (card_id or any(r in low for r in _REF)):
        action = "delete"
    elif any(k in low for k in _START) and (card_id or any(r in low for r in _REF)):
        action = "start"
    elif any(k in low for k in _DONE) and (card_id or any(r in low for r in _REF)):
        action = "done"
    else:
        return None

    # ключевые слова для поиска задачи по названию (без служебных)
    stop = set(_REF) | {"закрой", "закрыл", "удали", "работу", "работе", "сделай", "сделал"}
    kws = [t.lower() for t in _TOKEN.findall(text) if not _UUID.match(t) and t.lower() not in stop]
    return TaskCommand(action=action, card_id=card_id, keywords=kws)


def _stems(tokens) -> set[str]:
    """Грубый стемминг: первые 6 букв (чтобы «презентации» ≈ «презентацию»)."""
    return {t.lower()[:6] for t in tokens}


def _find_task(c: AppContainer, cmd: TaskCommand):
    if cmd.card_id:
        return c.repo.get_by_card(cmd.card_id) or c.repo.get(cmd.card_id)
    # по ключевым словам среди открытых задач
    kw = _stems(cmd.keywords)
    best, best_score = None, 0
    for t in c.repo.open():
        title_stems = _stems(_TOKEN.findall(t.title))
        score = len(title_stems & kw)
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= 1 else None


async def handle(message: Message, c: AppContainer, cmd: TaskCommand) -> bool:
    """Выполнить команду. Вернуть True, если обработали (не извлекать задачу)."""
    task = _find_task(c, cmd)

    # Задачи нет в памяти (например, после перезапуска), но есть UUID карточки —
    # действуем напрямую по карточке на доске.
    if task is None and cmd.card_id:
        try:
            if cmd.action == "done":
                await c.board.complete_card(cmd.card_id)
                await message.answer(f"✅ Закрыл задачу <code>{esc(cmd.card_id)}</code>.")
            elif cmd.action == "start":
                await c.board.move_card(cmd.card_id, TaskStatus.in_progress)
                await message.answer(f"▶️ Перевёл в работу <code>{esc(cmd.card_id)}</code>.")
            elif cmd.action == "delete":
                await c.board.delete_card(cmd.card_id)
                await message.answer(f"🗑️ Удалил задачу <code>{esc(cmd.card_id)}</code>.")
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("Команда по карточке %s не удалась: %s", cmd.card_id, e)
            await message.answer("Не нашёл такую карточку на доске 🙈")
            return True

    if task is None:
        # Это была команда (закрой/удали…), но цель не нашли — отвечаем, не плодим задачу
        await message.answer("Не понял, какую задачу. Укажите её ID или ключевые слова из названия.")
        return True

    if cmd.action == "done":
        await c.service.set_status(task, TaskStatus.done)
        await message.answer(f"✅ Готово: «{esc(task.title)}».")
        for line in c.game.complete(task):
            await message.answer(line)
    elif cmd.action == "start":
        await c.service.set_status(task, TaskStatus.in_progress)
        await message.answer(f"▶️ В работе: «{esc(task.title)}».")
    elif cmd.action == "delete":
        await c.service.delete_task(task)
        await message.answer(f"🗑️ Удалил: «{esc(task.title)}».")
    return True
