"""Inline-клавиатуры сценария подтверждения и управления задачами."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..domain.enums import TaskStatus
from .callback_data import BoardCD, ConfirmCD, ForgetCD, IntroCD, TaskCD


def confirm_keyboard(pid: str, *, claim_name: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=ConfirmCD(action="confirm", pid=pid))
    kb.button(text="✏️ Поправить", callback_data=ConfirmCD(action="edit", pid=pid))
    kb.button(text="❌ Отклонить", callback_data=ConfirmCD(action="reject", pid=pid))
    if claim_name:
        # Исполнитель неизвестен боту — даём кнопку «это я», чтобы человек закрепился
        short = claim_name[:20]
        kb.button(
            text=f"👋 Это я ({short})",
            callback_data=IntroCD(action="claim", pid=pid, name=short),
        )
        kb.adjust(2, 1, 1)
    else:
        kb.adjust(2, 1)
    return kb.as_markup()


def introduce_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👋 Представиться", callback_data=IntroCD(action="self"))
    return kb.as_markup()


def forget_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑️ Да, забыть всех", callback_data=ForgetCD(action="yes"))
    kb.button(text="↩️ Отмена", callback_data=ForgetCD(action="no"))
    kb.adjust(1)
    return kb.as_markup()


def duplicate_keyboard(pid: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔗 Та же — объединить", callback_data=ConfirmCD(action="dup_merge", pid=pid))
    kb.button(text="➕ Создать новую", callback_data=ConfirmCD(action="dup_new", pid=pid))
    kb.adjust(1)
    return kb.as_markup()


def clarify_keyboard(pid: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, завести", callback_data=ConfirmCD(action="clarify_yes", pid=pid))
    kb.button(text="🚫 Нет", callback_data=ConfirmCD(action="clarify_no", pid=pid))
    kb.adjust(2)
    return kb.as_markup()


def task_actions_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="▶️ В работу", callback_data=TaskCD(action="start", task_id=task_id).pack()),
            InlineKeyboardButton(text="✅ Готово", callback_data=TaskCD(action="done", task_id=task_id).pack()),
        ]]
    )


# ── Управление карточкой доски (мои задачи) ──────────────────────────────────
# action для статусов совпадает с TaskStatus.value, чтобы ставить «галочку».
_STATUS_BTN = {
    TaskStatus.todo: ("📋 К выполнению", "todo"),
    TaskStatus.in_progress: ("▶️ В работу", "in_progress"),
    TaskStatus.done: ("✅ Готово", "done"),
}


def board_task_keyboard(card_id: str, current: TaskStatus, *, confirm_delete: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура управления задачей: 3 статуса (с ✓ у текущего) + удаление.

    При confirm_delete показываем подтверждение удаления вместо обычного ряда.
    """
    if confirm_delete:
        rows = [[
            InlineKeyboardButton(text="🗑️ Да, удалить", callback_data=BoardCD(action="del_yes", cid=card_id).pack()),
            InlineKeyboardButton(text="↩️ Отмена", callback_data=BoardCD(action="del_no", cid=card_id).pack()),
        ]]
        return InlineKeyboardMarkup(inline_keyboard=rows)

    status_row = []
    for status, (label, action) in _STATUS_BTN.items():
        mark = "✓ " if status == current else ""
        status_row.append(
            InlineKeyboardButton(text=f"{mark}{label}", callback_data=BoardCD(action=action, cid=card_id).pack())
        )
    delete_row = [InlineKeyboardButton(text="🗑️ Удалить", callback_data=BoardCD(action="del", cid=card_id).pack())]
    return InlineKeyboardMarkup(inline_keyboard=[status_row, delete_row])
