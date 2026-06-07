"""Тесты вечерней сверки отчётов."""

from datetime import date

import pytest

from dirizher.container import AppContainer
from dirizher.domain.enums import TaskSource, TaskStatus
from dirizher.domain.models import SourceRef, TeamMember

TODAY = date(2026, 6, 7)
CHAT = 555


@pytest.fixture
async def c():
    cont = AppContainer()
    cont.team.register(TeamMember(user_id=1, username="maxim", full_name="Максим", aliases=["Максим"]))
    cont.team.register(TeamMember(user_id=2, username="dasha", full_name="Дарья", aliases=["Даша"]))
    for msg in ["Максим, сделай авторизацию к пятнице", "Даша, подготовь макет к пятнице"]:
        p = (await cont.service.ingest(msg, SourceRef(source=TaskSource.chat, chat_id=CHAT), today=TODAY))[0]
        await cont.service.create_on_board(p.task)
    return cont


async def test_report_marks_done(c):
    notes = await c.reconciliation.record_report(CHAT, "maxim", "авторизацию сделал, готово", today=TODAY)
    assert notes  # есть изменение статуса
    maxim_tasks = c.repo.by_assignee("maxim")
    assert maxim_tasks[0].status is TaskStatus.done


async def test_digest_tags_silent(c):
    await c.reconciliation.record_report(CHAT, "maxim", "всё готово", today=TODAY)
    digest, silent = c.reconciliation.evening_digest(CHAT, today=TODAY)
    assert "@dasha" in silent          # Даша не отписалась
    assert "@maxim" not in silent      # Максим отписался
    assert "Вечерняя сверка" in digest
