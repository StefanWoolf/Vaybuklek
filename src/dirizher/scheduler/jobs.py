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


def _active_chats(c: AppContainer) -> set[int]:
    """Чаты для авто-рассылок: явный team_chat_id + чаты с открытыми задачами."""
    chats: set[int] = set()
    if c.settings.telegram.team_chat_id:
        chats.add(c.settings.telegram.team_chat_id)
    for t in c.repo.open():
        for s in t.sources:
            if s.chat_id:
                chats.add(s.chat_id)
    return chats


async def run_morning_digest(c: AppContainer, *, today: date | None = None) -> int:
    """Утренняя сводка: задачи на сегодня и просроченные, по исполнителям.

    Триггерится n8n-кроном (POST /jobs/morning-digest) или APScheduler.
    Возвращает число чатов, куда отправлена сводка."""
    today = today or date.today()
    if c.bot is None:
        log.warning("Утренняя сводка: бот не инициализирован")
        return 0

    chats = _active_chats(c)
    if not chats:
        return 0

    due_today = [t for t in c.repo.open() if t.deadline == today]
    overdue = [t for t in c.repo.open() if t.deadline and t.deadline < today]

    lines = [f"☀️ <b>Доброе утро!</b> План на {today.isoformat()}", ""]
    if not due_today and not overdue:
        lines.append("На сегодня дедлайнов нет — спокойный день 🙂")
    else:
        if overdue:
            lines.append("🔴 <b>Просрочено:</b>")
            for t in overdue:
                lines.append(f"  • {_esc(t.title)} — {c.team.mention_for(t.assignee)}")
            lines.append("")
        if due_today:
            lines.append("📅 <b>Сегодня дедлайн:</b>")
            for t in due_today:
                lines.append(f"  • {_esc(t.title)} — {c.team.mention_for(t.assignee)}")
    text = "\n".join(lines).strip()

    for chat_id in chats:
        await c.bot.send_message(chat_id, text)
    log.info("Утренняя сводка разослана в чатов: %d", len(chats))
    return len(chats)


async def run_leaderboard_post(c: AppContainer) -> int:
    """Опубликовать игровой лидерборд в активные чаты (п.10 × п.5).

    Триггерится n8n-кроном (POST /jobs/leaderboard) или APScheduler по пятницам."""
    if c.bot is None:
        log.warning("Лидерборд: бот не инициализирован")
        return 0
    if not c.game.leaderboard(1):
        return 0  # некого награждать — не спамим
    chats = _active_chats(c)
    text = "🏁 <b>Итоги недели</b>\n\n" + c.game.render_leaderboard()
    for chat_id in chats:
        await c.bot.send_message(chat_id, text)
    log.info("Лидерборд разослан в чатов: %d", len(chats))
    return len(chats)


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

    # активные чаты — явный team_chat_id + чаты с открытыми задачами
    chats = _active_chats(c)

    for chat_id in chats:
        digest, _silent = c.reconciliation.evening_digest(chat_id, today=today)
        await c.bot.send_message(chat_id, digest)
    log.info("Вечерняя сверка разослана в чатов: %d", len(chats))
    return len(chats)
