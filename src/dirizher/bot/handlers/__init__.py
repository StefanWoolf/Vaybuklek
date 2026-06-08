"""Роутеры бота. Порядок включения важен: состояние/колбэки до catch-all текста."""

from aiogram import Router

from . import callbacks, commands, meeting, messages, onboarding, voice


def build_root_router() -> Router:
    root = Router(name="root")
    # Команды, онбординг и колбэки — раньше общего обработчика текста
    root.include_router(commands.router)
    root.include_router(onboarding.router)
    root.include_router(callbacks.router)
    # meeting — до voice (ловит голос в режиме регистрации) и до messages (ссылка Телемоста)
    root.include_router(meeting.router)
    root.include_router(voice.router)
    root.include_router(messages.router)
    return root


__all__ = ["build_root_router"]
