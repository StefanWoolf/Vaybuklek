"""Тесты интерактивного списка задач и распознавания вопроса «мои задачи»."""

import pytest

from dirizher.bot.handlers.commands import _card_belongs_to
from dirizher.bot.handlers.messages import _is_my_tasks_query
from dirizher.bot.keyboards import board_task_keyboard
from dirizher.domain.enums import TaskStatus
from dirizher.domain.models import TeamMember
from dirizher.integrations.yougile import BoardCard


@pytest.mark.parametrize("text", [
    "какие у меня задачи?",
    "Какие у меня таски?",
    "мои задачи",
    "покажи мои таски",
    "что у меня по задачам",
    "список задач",
])
def test_my_tasks_query_detected(text):
    assert _is_my_tasks_query(text) is True


@pytest.mark.parametrize("text", [
    "Данила сделай бота к среде",
    "поставь задачу на завтра",
    "привет",
    "закрой задачу по презентации",
])
def test_my_tasks_query_not_triggered(text):
    assert _is_my_tasks_query(text) is False


def test_card_belongs_to_by_alias():
    m = TeamMember(user_id=1, username="danya", full_name="Данила Скиба", aliases=["Дэн"])
    assert _card_belongs_to(BoardCard(id="x", title="t", assignee="Данила Скиба"), m) is True
    assert _card_belongs_to(BoardCard(id="x", title="t", assignee="Дэн"), m) is True
    assert _card_belongs_to(BoardCard(id="x", title="t", assignee="Андрей"), m) is False
    assert _card_belongs_to(BoardCard(id="x", title="t", assignee=None), m) is False


def test_board_keyboard_checkmark_on_current_status():
    kb = board_task_keyboard("card1", TaskStatus.in_progress)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    # галочка только у текущего статуса
    assert any(l.startswith("✓ ") and "В работу" in l for l in labels)
    assert not any(l.startswith("✓ ") and "Готово" in l for l in labels)
    assert any("Удалить" in l for l in labels)


def test_board_keyboard_delete_confirmation():
    kb = board_task_keyboard("card1", TaskStatus.todo, confirm_delete=True)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Да, удалить" in l for l in labels)
    assert any("Отмена" in l for l in labels)
