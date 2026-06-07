"""Тесты ядра: классификация, дедуп, создание, правка, нагрузка."""

from datetime import date

import pytest

from dirizher.container import AppContainer
from dirizher.domain.enums import TaskSource
from dirizher.domain.models import SourceRef, TeamMember
from dirizher.services.task_service import Outcome

TODAY = date(2026, 6, 7)


@pytest.fixture
def c():
    cont = AppContainer()
    cont.team.register(TeamMember(user_id=1, username="maxim", full_name="Максим", aliases=["Максим"]))
    cont.team.register(TeamMember(user_id=2, username="dasha", full_name="Дарья", aliases=["Даша"]))
    return cont


def _src():
    return SourceRef(source=TaskSource.chat, chat_id=555)


async def test_new_task_then_create(c):
    processed = await c.service.ingest("Максим, сделай авторизацию к четвергу", _src(), today=TODAY)
    assert len(processed) == 1 and processed[0].outcome is Outcome.new
    task = processed[0].task
    assert task.assignee == "maxim"  # нормализован к username команды
    created = await c.service.create_on_board(task)
    assert created.board_card_id is not None
    assert len(await c.board.list_cards()) == 1


async def test_low_confidence(c):
    processed = await c.service.ingest("нужно поправить баг", _src(), today=TODAY)
    assert processed and processed[0].outcome is Outcome.low_confidence


async def test_dedup_after_create(c):
    p1 = (await c.service.ingest("Максим, сделай авторизацию к четвергу", _src(), today=TODAY))[0]
    await c.service.create_on_board(p1.task)
    p2 = (await c.service.ingest("Максим, сделай авторизацию к четвергу", _src(), today=TODAY))[0]
    assert p2.outcome is Outcome.duplicate
    assert p2.duplicate_of is not None


async def test_apply_correction_changes_deadline(c):
    p = (await c.service.ingest("Даша, подготовь макет к понедельнику", _src(), today=TODAY))[0]
    assert p.task.deadline == date(2026, 6, 8)
    await c.service.apply_correction(p.task, "перенеси на пятницу", today=TODAY)
    assert p.task.deadline == date(2026, 6, 12)


async def test_workload_warning(c):
    for i in range(5):
        p = (await c.service.ingest(f"Максим, сделай задачу{i} к пятнице", _src(), today=TODAY))[0]
        await c.service.create_on_board(p.task)
    warn = c.service.workload_warning("maxim")
    assert warn is not None and "maxim" in warn and "5 открытых" in warn
