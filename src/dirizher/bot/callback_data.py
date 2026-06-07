"""Структурированные callback-данные для inline-кнопок."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class ConfirmCD(CallbackData, prefix="cf"):
    # confirm | edit | reject | dup_merge | dup_new | clarify_yes | clarify_no
    action: str
    pid: str


class TaskCD(CallbackData, prefix="tk"):
    # done | start
    action: str
    task_id: str


class IntroCD(CallbackData, prefix="intro"):
    # self  — представиться за себя
    # claim — «это я» закрепить неизвестного исполнителя из карточки (pid)
    action: str
    pid: str = "-"
    name: str = ""
