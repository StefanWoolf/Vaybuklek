"""Тесты извлечения задач mock-провайдером (эвристика)."""

from datetime import date

import pytest

from dirizher.domain.enums import Priority
from dirizher.llm.base import ExtractionContext
from dirizher.llm.mock_provider import MockLLMProvider

TODAY = date(2026, 6, 7)  # воскресенье


@pytest.fixture
def ctx():
    return ExtractionContext(today=TODAY)


async def test_basic_task_with_assignee_and_deadline(ctx):
    prov = MockLLMProvider()
    tasks = await prov.extract_tasks("Максим, сделай авторизацию к четвергу", ctx)
    assert len(tasks) == 1
    t = tasks[0]
    assert "авторизаци" in t.task.lower()
    assert t.assignee == "Максим"
    assert t.deadline == date(2026, 6, 11)  # ближайший четверг
    assert t.confidence >= 0.7


async def test_priority_urgent(ctx):
    prov = MockLLMProvider()
    tasks = await prov.extract_tasks("Даша, подготовь макет срочно", ctx)
    assert tasks[0].priority is Priority.high


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("к понедельнику", date(2026, 6, 8)),
        ("к вторнику", date(2026, 6, 9)),
        ("до пятницы", date(2026, 6, 12)),
        ("завтра", date(2026, 6, 8)),
        ("послезавтра", date(2026, 6, 9)),
    ],
)
async def test_deadline_parsing(ctx, phrase, expected):
    prov = MockLLMProvider()
    tasks = await prov.extract_tasks(f"сделай отчёт {phrase}", ctx)
    assert tasks and tasks[0].deadline == expected


async def test_non_task_ignored(ctx):
    prov = MockLLMProvider()
    tasks = await prov.extract_tasks("всем привет, как дела?", ctx)
    assert tasks == []
