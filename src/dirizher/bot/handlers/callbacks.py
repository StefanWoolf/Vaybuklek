"""Колбэки кнопок: подтверждение, правка, дубли, уточнение, статусы задач."""

from __future__ import annotations

from html import escape as esc

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ...container import AppContainer
from ...domain.enums import TaskStatus
from ...logging_setup import get_logger
from .. import keyboards as kb
from .. import text as tx
from ..callback_data import ConfirmCD, TaskCD
from ..states import EditTask

router = Router(name="callbacks")
log = get_logger("dirizher.bot.callbacks")


async def _finish(cb: CallbackQuery, text: str) -> None:
    if isinstance(cb.message, Message):
        await cb.message.edit_text(text)
    await cb.answer()


# ── Сценарий подтверждения ───────────────────────────────────────────────────
@router.callback_query(ConfirmCD.filter())
async def on_confirm(cb: CallbackQuery, callback_data: ConfirmCD, c: AppContainer, state: FSMContext) -> None:
    action, pid = callback_data.action, callback_data.pid
    pending = c.pending.get(pid)
    if pending is None:
        await cb.answer("Карточка устарела 🙈", show_alert=False)
        return

    if action == "confirm":
        c.pending.pop(pid)
        created = await c.service.create_on_board(pending.task)
        await _finish(cb, tx.render_created(created))
        if isinstance(cb.message, Message):
            for name in created.assignees:
                warning = c.service.workload_warning(name)
                if warning:
                    await cb.message.answer(warning)

    elif action == "reject":
        c.pending.pop(pid)
        await _finish(cb, f"❌ Отклонено: «{esc(pending.task.title)}»")

    elif action == "edit":
        await state.set_state(EditTask.waiting_correction)
        await state.update_data(pid=pid)
        await cb.message.answer(
            "✏️ Что поправить? Напишите текстом или пришлите голосовое.\n"
            "Например: «перенеси на пятницу», «назначь на Дашу», «сделай срочной»."
        )
        await cb.answer()

    elif action == "dup_merge":
        c.pending.pop(pid)
        existing = c.repo.get(pending.duplicate_of_id or "")
        if existing:
            merged = await c.service.merge_duplicate(existing, pending.source)
            await _finish(cb, f"🔗 Объединил с «{esc(merged.title)}». Источников: {len(merged.sources)}.")
        else:
            created = await c.service.create_on_board(pending.task)
            await _finish(cb, tx.render_created(created))

    elif action == "dup_new":
        c.pending.pop(pid)
        created = await c.service.create_on_board(pending.task)
        await _finish(cb, tx.render_created(created))

    elif action == "clarify_yes":
        c.pending.pop(pid)
        created = await c.service.create_on_board(pending.task)
        await _finish(cb, tx.render_created(created))

    elif action == "clarify_no":
        c.pending.pop(pid)
        await _finish(cb, "🚫 Понял, не завожу.")


# ── Приём правки (FSM) ───────────────────────────────────────────────────────
@router.message(EditTask.waiting_correction, F.text)
async def on_correction(message: Message, c: AppContainer, state: FSMContext) -> None:
    data = await state.get_data()
    pid = data.get("pid")
    await state.clear()
    pending = c.pending.get(pid or "")
    if pending is None:
        await message.answer("Карточка для правки не найдена 🙈")
        return
    await c.service.apply_correction(pending.task, message.text or "")
    await message.answer(
        "Переформулировал:\n\n" + tx.render_task_card(pending.task, header="✏️ Поправленная задача"),
        reply_markup=kb.confirm_keyboard(pending.pid),
    )


# ── Управление задачей: перенос между колонками и удаление ───────────────────
_STATUS_ACTIONS = {
    "todo": (TaskStatus.todo, "📋 К выполнению"),
    "in_progress": (TaskStatus.in_progress, "▶️ В работе"),
    "done": (TaskStatus.done, "✅ Готово"),
}


@router.callback_query(TaskCD.filter())
async def on_task_action(cb: CallbackQuery, callback_data: TaskCD, c: AppContainer) -> None:
    task = c.repo.get(callback_data.task_id)
    if task is None:
        await cb.answer("Задача не найдена 🙈", show_alert=False)
        return

    action = callback_data.action

    # Перенос между колонками К выполнению / В работе / Готово
    if action in _STATUS_ACTIONS:
        status, label = _STATUS_ACTIONS[action]
        if task.status == status:
            await cb.answer(f"Уже здесь: {label}")
            return
        await c.service.set_status(task, status)
        await cb.answer(f"Перенёс → {label}")
        if isinstance(cb.message, Message):
            await cb.message.edit_reply_markup(reply_markup=kb.task_actions_keyboard(task))
        return

    # Удаление с подтверждением
    if action == "delete":
        await cb.answer()
        if isinstance(cb.message, Message):
            await cb.message.edit_reply_markup(reply_markup=kb.confirm_delete_keyboard(task.id))
        return
    if action == "delete_no":
        await cb.answer("Отменил удаление")
        if isinstance(cb.message, Message):
            await cb.message.edit_reply_markup(reply_markup=kb.task_actions_keyboard(task))
        return
    if action == "delete_yes":
        await c.service.delete_task(task)
        await cb.answer("🗑 Удалено")
        if isinstance(cb.message, Message):
            await cb.message.edit_text(f"🗑 Задача удалена: «{esc(task.title)}»")
        return
