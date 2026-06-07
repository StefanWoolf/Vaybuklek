"""Фоновые задания: напоминания о дедлайнах и вечерняя сверка.

Задания работают поверх контейнера и шлют сообщения через container.bot.
Они же вызываются HTTP-API (n8n cron), поэтому вынесены отдельными функциями.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from html import escape as _esc

from ..container import AppContainer
from ..domain.models import Task
from ..logging_setup import get_logger

log = get_logger("dirizher.jobs")


def _target_chat(c: AppContainer, task: Task) -> int | None:
    """Чат для уведомления: явный team_chat_id или чат-источник задачи."""
    if c.settings.telegram.team_chat_id:
        return c.settings.telegram.team_chat_id
    for s in task.sources:
        if s.chat_id:
            return s.chat_id
    return None


async def run_reminders(c: AppContainer, *, today: date | None = None) -> int:
    """Напомнить об открытых задачах, дедлайн которых близко/просрочен.

    Возвращает число отправленных напоминаний.
    """
    today = today or date.today()
    horizon_day = today + timedelta(days=max(1, c.settings.schedule.remind_before_hours // 24))
    sent = 0
    now = datetime.now(timezone.utc)

    for task in c.repo.due_on_or_before(horizon_day):
        # не напоминаем чаще раза в ~20 часов
        if task.reminded_at and (now - task.reminded_at) < timedelta(hours=20):
            continue
        chat_id = _target_chat(c, task)
        if not chat_id or c.bot is None:
            continue
        mention = c.team.mention_for(task.assignee)  # уже HTML-безопасно
        overdue = task.deadline and task.deadline < today
        head = "⏰ <b>Просрочено!</b>" if overdue else "⏰ <b>Скоро дедлайн</b>"
        dl = task.deadline_display() if task.deadline else "—"
        await c.bot.send_message(
            chat_id,
            f"{head}\n📋 {_esc(task.title)}\n👤 {mention} · 📅 до {_esc(dl)}\n"
            f"Отметьте статус, когда будет готово.",
        )
        task.reminded_at = now
        sent += 1
    log.info("Напоминаний отправлено: %d", sent)
    return sent


async def run_evening_reconciliation(c: AppContainer, *, today: date | None = None) -> int:
    """Разослать вечернюю сводку по всем активным чатам. Возвращает число чатов."""
    today = today or date.today()
    if c.bot is None:
        log.warning("Вечерняя сверка: бот не инициализирован")
        return 0

    # активные чаты — те, где есть открытые задачи (по источникам)
    chats: set[int] = set()
    if c.settings.telegram.team_chat_id:
        chats.add(c.settings.telegram.team_chat_id)
    for t in c.repo.open():
        for s in t.sources:
            if s.chat_id:
                chats.add(s.chat_id)

    for chat_id in chats:
        digest, _silent = c.reconciliation.evening_digest(chat_id, today=today)
        await c.bot.send_message(chat_id, digest)
    log.info("Вечерняя сверка разослана в чатов: %d", len(chats))
    return len(chats)
