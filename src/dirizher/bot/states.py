"""FSM-состояния бота."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class EditTask(StatesGroup):
    """Пользователь нажал «Поправить» и пишет уточнение (текст или голос)."""

    waiting_correction = State()


class Introduce(StatesGroup):
    """Знакомство: участник присылает email доски YouGile и прозвища."""

    waiting_details = State()


class EnrollVoice(StatesGroup):
    """Регистрация голоса: участник присылает короткое голосовое для отпечатка."""

    waiting_voice = State()
