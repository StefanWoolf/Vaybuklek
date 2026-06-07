"""Сборка и запуск Telegram-бота (aiogram 3)."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from ..container import AppContainer
from ..logging_setup import get_logger
from .handlers import build_root_router

log = get_logger("dirizher.bot")


def build_dispatcher(container: AppContainer) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    # Прокидываем контейнер в каждый хендлер как kwarg `c`
    dp["c"] = container
    dp.include_router(build_root_router())
    return dp


def build_bot(container: AppContainer) -> Bot:
    token = container.settings.telegram.bot_token
    return Bot(token=token, default=DefaultBotProperties(parse_mode=None))


async def run_polling(container: AppContainer) -> None:
    bot = build_bot(container)
    container.bot = bot
    dp = build_dispatcher(container)
    me = await bot.get_me()
    log.info("Бот @%s запущен (polling)", me.username)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
