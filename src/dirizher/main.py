"""Точка входа Дирижёра.

• Нет Telegram-токена → консольный симулятор (демо без сети).
• mode=polling → бот на long-polling + API (для job-триггеров n8n) + планировщик.
• mode=webhook → API принимает апдейты от n8n (/ingest/telegram) + планировщик.
"""

from __future__ import annotations

import asyncio

from .config import get_settings
from .container import AppContainer
from .logging_setup import get_logger, setup_logging

log = get_logger("dirizher.main")


async def amain() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    log.info("Дирижёр стартует · %s", settings.mode_banner())

    if settings.telegram.is_mock:
        log.warning("Telegram-токен не задан → консольный симулятор. См. README.")
        from .cli.simulator import main as sim_main

        await sim_main()
        return

    import uvicorn

    from .api.server import create_api
    from .bot.app import build_bot, build_dispatcher
    from .scheduler.scheduler import build_scheduler

    container = AppContainer(settings)
    bot = build_bot(container)
    dp = build_dispatcher(container)
    container.bot = bot
    container.dp = dp

    scheduler = build_scheduler(container)
    scheduler.start()

    api = create_api(container)
    uv = uvicorn.Server(
        uvicorn.Config(
            api,
            host=settings.api.host,
            port=settings.api.port,
            log_level=settings.log_level.lower(),
        )
    )

    coros = [uv.serve()]
    if settings.telegram.mode == "polling":
        me = await bot.get_me()
        log.info("Бот @%s запущен (polling) · API на %s:%s", me.username, settings.api.host, settings.api.port)
        coros.append(dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()))
    else:
        log.info("Режим webhook · апдейты ждём на POST /ingest/telegram · API %s:%s",
                 settings.api.host, settings.api.port)

    try:
        await asyncio.gather(*coros)
    finally:
        scheduler.shutdown(wait=False)
        await container.aclose()


def run() -> None:
    try:
        asyncio.run(amain())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлено пользователем.")


if __name__ == "__main__":
    run()
