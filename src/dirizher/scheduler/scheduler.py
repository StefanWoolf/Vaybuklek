"""Планировщик APScheduler: cron-триггеры напоминаний и вечерней сверки.

Это «локальный» оркестратор времени. В проде те же задания может дёргать n8n
через HTTP-API (см. api/server.py) — тогда планировщик можно отключить.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..container import AppContainer
from ..logging_setup import get_logger
from .jobs import run_evening_reconciliation, run_reminders

log = get_logger("dirizher.scheduler")


def build_scheduler(c: AppContainer) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=c.settings.timezone)

    sched.add_job(
        run_reminders,
        CronTrigger.from_crontab(c.settings.schedule.reminder_cron, timezone=c.settings.timezone),
        args=[c],
        id="reminders",
        replace_existing=True,
    )
    sched.add_job(
        run_evening_reconciliation,
        CronTrigger.from_crontab(
            c.settings.schedule.evening_reconcile_cron, timezone=c.settings.timezone
        ),
        args=[c],
        id="evening_reconciliation",
        replace_existing=True,
    )
    log.info(
        "Планировщик: напоминания [%s], вечерняя сверка [%s]",
        c.settings.schedule.reminder_cron,
        c.settings.schedule.evening_reconcile_cron,
    )
    return sched
