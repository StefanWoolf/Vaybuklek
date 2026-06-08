"""Автономные задания (п.5) и начисление XP при сверке (п.10 × ядро)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dirizher.container import AppContainer
from dirizher.domain.enums import Priority, TaskSource, TaskStatus
from dirizher.domain.models import SourceRef, Task, TeamMember
from dirizher.scheduler.jobs import run_leaderboard_post, run_morning_digest

TODAY = date(2026, 6, 8)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_):
        self.sent.append((chat_id, text))


@pytest.fixture
def c():
    cont = AppContainer()
    cont.team.register(TeamMember(user_id=1, username="danya", full_name="Данила"))
    cont.bot = FakeBot()
    return cont


def _add(c, title, *, deadline=None, assignee="danya"):
    t = Task(
        title=title,
        assignee=assignee,
        deadline=deadline,
        sources=[SourceRef(source=TaskSource.chat, chat_id=555)],
    )
    c.repo.add(t)
    return t


async def test_morning_digest_lists_today_and_overdue(c):
    _add(c, "Срочный фикс", deadline=TODAY)
    _add(c, "Просроченное", deadline=TODAY - timedelta(days=2))
    _add(c, "На потом", deadline=TODAY + timedelta(days=5))
    n = await run_morning_digest(c, today=TODAY)
    assert n == 1
    text = c.bot.sent[0][1]
    assert "Срочный фикс" in text and "Просроченное" in text
    assert "На потом" not in text  # не сегодня и не просрочено


async def test_morning_digest_quiet_when_nothing(c):
    _add(c, "На потом", deadline=TODAY + timedelta(days=5))
    await run_morning_digest(c, today=TODAY)
    assert "спокойный день" in c.bot.sent[0][1]


async def test_leaderboard_post_only_when_players(c):
    # пусто — ничего не постим
    assert await run_leaderboard_post(c) == 0
    assert c.bot.sent == []
    # появился игрок с очками — постим
    c.game.complete(_add(c, "Готовое", deadline=TODAY), today=TODAY)
    assert await run_leaderboard_post(c) == 1
    assert "Лидерборд" in c.bot.sent[-1][1]


async def test_report_awards_xp(c):
    """Закрытие задачи через вечерний отчёт начисляет XP (через reconciliation)."""
    _add(c, "Авторизация", deadline=TODAY)
    notes = await c.reconciliation.record_report(555, "danya", "авторизацию сделал", today=TODAY)
    assert any("Готово" in n for n in notes)
    assert any("XP" in n for n in notes)  # поздравление прилетело в заметки
    assert c.game.profile_for("danya").tasks_done >= 1
