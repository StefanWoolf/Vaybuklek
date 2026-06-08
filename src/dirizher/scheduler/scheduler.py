"""Планировщик APScheduler: cron-триггеры напоминаний и вечерней сверки.

Это «локальный» оркестратор времени. В проде те же задания может дёргать n8n
через HTTP-API (см. api/server.py) — тогда планировщик можно отключить.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..container import AppContainer
from ..logging_setup import get_logger
from .jobs import (
    run_evening_reconciliation,
    run_leaderboard_post,
    run_morning_digest,
    run_reminders,
)

log = get_logger("dirizher.scheduler")


def build_scheduler(c: AppContainer) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=c.settings.timezone)
    tz = c.settings.timezone
    sch = c.settings.schedule

    sched.add_job(
        run_morning_digest,
        CronTrigger.from_crontab(sch.morning_digest_cron, timezone=tz),
        args=[c],
        id="morning_digest",
        replace_existing=True,
    )
    sched.add_job(
        run_reminders,
        CronTrigger.from_crontab(sch.reminder_cron, timezone=tz),
        args=[c],
        id="reminders",
        replace_existing=True,
    )
    sched.add_job(
        run_evening_reconciliation,
        CronTrigger.from_crontab(sch.evening_reconcile_cron, timezone=tz),
        args=[c],
        id="evening_reconciliation",
        replace_existing=True,
    )
    sched.add_job(
        run_leaderboard_post,
        CronTrigger.from_crontab(sch.leaderboard_cron, timezone=tz),
        args=[c],
        id="leaderboard",
        replace_existing=True,
    )
    log.info(
        "Планировщик: утро [%s], напоминания [%s], вечер [%s], лидерборд [%s]",
        sch.morning_digest_cron,
        sch.reminder_cron,
        sch.evening_reconcile_cron,
        sch.leaderboard_cron,
    )
    return sched
