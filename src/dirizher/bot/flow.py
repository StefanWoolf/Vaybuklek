"""Общий поток предъявления извлечённых задач (используется текстом и голосом).

Решает, что делать с каждой обработанной задачей в зависимости от режима чата:
- авто-режим (True): новые задачи и объединения применяются сразу;
- ручной режим (False): показываем карточку и кнопки подтверждения/правки.

Важно: порог уверенности — отдельный страж от фантомных задач: даже в
авто-режиме задачи с низкой уверенностью выносятся на уточнение.
"""

from __future__ import annotations

from html import escape as esc

from aiogram import Bot

from ..container import AppContainer
from ..services.task_service import Outcome, ProcessedTask
from . import keyboards as kb
from . import text as tx


async def notify_workload(bot: Bot, c: AppContainer, assignee: str | None, chat_id: int) -> None:
    """Предупредить чат о перегрузке исполнителя (если есть)."""
    warning = c.service.workload_warning(assignee)
    if warning:
        await bot.send_message(chat_id, warning)


async def present(bot: Bot, c: AppContainer, processed: list[ProcessedTask], chat_id: int) -> None:
    if not processed:
        return
    auto = c.mode.is_auto(chat_id)
    for p in processed:
        if p.outcome is Outcome.low_confidence:
            await _ask_clarify(bot, c, p, chat_id)
        elif p.outcome is Outcome.duplicate:
            if auto and p.duplicate_of:
                merged = await c.service.merge_duplicate(p.duplicate_of, p.task.sources[0])
                await bot.send_message(
                    chat_id,
                    f"♻️ Объединил с существующей: «{esc(merged.title)}» (источники: "
                    f"{len(merged.sources)}).",
                )
            else:
                await _ask_duplicate(bot, c, p, chat_id)
        else:  # new
            if auto:
                created = await c.service.create_on_board(p.task)
                await bot.send_message(chat_id, tx.render_created(created))
                await notify_workload(bot, c, created.assignee, chat_id)
            else:
                await _ask_confirm(bot, c, p, chat_id)


def _unknown_assignee(c: AppContainer, p: ProcessedTask) -> str | None:
    """Первый исполнитель, которого бот не знает (нельзя тегать) — иначе None.

    Поддерживает несколько исполнителей через запятую: ищем первого незнакомого.
    """
    from ..services.task_service import _split_assignees

    for name in _split_assignees(p.task.assignee):
        if c.team.resolve(name) is None:
            return name
    return None


async def _ask_confirm(bot: Bot, c: AppContainer, p: ProcessedTask, chat_id: int) -> None:
    pending = c.pending.put(p, chat_id)
    unknown = _unknown_assignee(c, p)
    body = tx.render_processed(p)
    if unknown:
        body += (
            f"\n\n⚠️ Я пока не знаю, кто такой «{esc(unknown)}». "
            f"Пусть он нажмёт «👋 Это я», или поправьте исполнителя."
        )
    await bot.send_message(
        chat_id,
        body,
        reply_markup=kb.confirm_keyboard(pending.pid, claim_name=unknown),
    )


async def _ask_duplicate(bot: Bot, c: AppContainer, p: ProcessedTask, chat_id: int) -> None:
    pending = c.pending.put(p, chat_id)
    await bot.send_message(
        chat_id,
        tx.render_processed(p),
        reply_markup=kb.duplicate_keyboard(pending.pid),
    )


async def _ask_clarify(bot: Bot, c: AppContainer, p: ProcessedTask, chat_id: int) -> None:
    pending = c.pending.put(p, chat_id)
    await bot.send_message(
        chat_id,
        tx.render_processed(p),
        reply_markup=kb.clarify_keyboard(pending.pid),
    )
