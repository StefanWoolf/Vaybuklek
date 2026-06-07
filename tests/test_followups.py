"""Тесты исправлений по фидбэку: правка-заголовок, мульти-исполнители, команды."""

from datetime import date

import pytest

from dirizher.bot import task_commands
from dirizher.container import AppContainer
from dirizher.domain.enums import Priority, TaskSource, TaskStatus
from dirizher.domain.models import SourceRef, TeamMember
from dirizher.services.task_service import Outcome, _split_assignees

TODAY = date(2026, 6, 7)


@pytest.fixture
def c():
    cont = AppContainer()
    cont.team.register(TeamMember(user_id=1, username="danya", full_name="Данила", aliases=["Данила", "Даня"]))
    cont.team.register(TeamMember(user_id=2, username="andrey", full_name="Андрей", aliases=["Андрей"]))
    return cont


def _src():
    return SourceRef(source=TaskSource.chat, chat_id=1)


# ── #1: правка не затирает заголовок ─────────────────────────────────────────
async def test_correction_keeps_title(c):
    p = (await c.service.ingest("Данила напиши телеграм бота к среде", _src(), today=TODAY))[0]
    title_before = p.task.title
    await c.service.apply_correction(p.task, "приоритет сделай выше", today=TODAY)
    assert p.task.title == title_before           # заголовок НЕ изменился
    assert p.task.priority is Priority.high        # приоритет повышен


async def test_correction_explicit_rename(c):
    p = (await c.service.ingest("Данила напиши бота к среде", _src(), today=TODAY))[0]
    await c.service.apply_correction(p.task, "переименуй в Сделать MVP бота", today=TODAY)
    assert "MVP" in p.task.title


# ── #3: несколько исполнителей ───────────────────────────────────────────────
def test_split_assignees():
    assert _split_assignees("Данила и Андрей") == ["Данила", "Андрей"]
    assert _split_assignees("Данила, Андрей") == ["Данила", "Андрей"]
    assert _split_assignees("@danya") == ["@danya"]
    assert _split_assignees(None) == []


async def test_multi_assignee_creates_two(c):
    processed = await c.service.ingest("Данила и Андрей сделайте фикс API к завтра", _src(), today=TODAY)
    assignees = {p.task.assignee for p in processed}
    assert assignees == {"danya", "andrey"}        # нормализованы к username
    assert all(p.outcome is Outcome.new for p in processed)


# ── #4/#5: команды над существующими задачами ───────────────────────────────
def test_detect_commands():
    assert task_commands.detect("Закрой задачу 8a51c61e-ba21-4aa2-8450-c1b4b88741ad").action == "done"
    assert task_commands.detect("удали задачу про презентацию").action == "delete"
    assert task_commands.detect("возьми в работу задачу по API").action == "start"
    # обычное создание не должно распознаваться как команда
    assert task_commands.detect("Данила сделай презу бота до пятницы") is None


async def test_close_by_keyword(c):
    p = (await c.service.ingest("Данила сделай презентацию бота к среде", _src(), today=TODAY))[0]
    task = await c.service.create_on_board(p.task)
    found = task_commands._find_task(c, task_commands.detect("закрыл задачу по презентации"))
    assert found is not None and found.id == task.id


async def test_delete_removes_task(c):
    p = (await c.service.ingest("Данила сделай фикс API к среде", _src(), today=TODAY))[0]
    task = await c.service.create_on_board(p.task)
    assert len(await c.board.list_cards()) == 1
    await c.service.delete_task(task)
    assert c.repo.get(task.id) is None
    assert len(await c.board.list_cards()) == 0
