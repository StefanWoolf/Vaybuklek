"""Геймификация (п.10): XP, уровни, ачивки, лидерборд, идемпотентность."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dirizher.domain.enums import Priority
from dirizher.domain.models import Task, TeamMember
from dirizher.repository import TeamRegistry
from dirizher.services.gamification import (
    BASE_XP,
    GamificationService,
    GameStore,
    is_on_time,
    rank_for,
    xp_for_completion,
    _streak_after,
)

TODAY = date(2026, 6, 8)


@pytest.fixture
def game(tmp_path):
    team = TeamRegistry()
    team.register(TeamMember(user_id=1, username="danya_skiba", full_name="Данила Скиба"))
    team.register(TeamMember(user_id=2, username="andrey", full_name="Андрей", aliases=["энди"]))
    store = GameStore(str(tmp_path / "game.json"))
    return GamificationService(store, team)


def _task(title="Задача", assignee="danya_skiba", priority=Priority.medium, deadline=None):
    return Task(title=title, assignee=assignee, priority=priority, deadline=deadline)


# ── Чистое ядро ──────────────────────────────────────────────────────────────
def test_xp_priority_and_ontime():
    high_ontime = _task(priority=Priority.high, deadline=TODAY)
    assert xp_for_completion(high_ontime, on_time=True) == BASE_XP + 15 + 10
    low_late = _task(priority=Priority.low)
    assert xp_for_completion(low_late, on_time=False) == BASE_XP


def test_on_time_rules():
    assert is_on_time(_task(deadline=None), TODAY) is True  # без срока — не штрафуем
    assert is_on_time(_task(deadline=TODAY), TODAY) is True
    assert is_on_time(_task(deadline=TODAY - timedelta(days=1)), TODAY) is False


def test_rank_progression():
    assert rank_for(0)[0] == 1
    assert rank_for(50)[1] == "Боец"
    assert rank_for(1000)[1] == "Легенда"
    assert rank_for(1000)[3] is None  # дальше некуда


def test_streak_logic():
    assert _streak_after(None, TODAY, 0) == 1
    assert _streak_after(TODAY - timedelta(days=1), TODAY, 3) == 4  # день в день — растёт
    assert _streak_after(TODAY - timedelta(days=3), TODAY, 5) == 1  # разрыв — сброс
    assert _streak_after(TODAY, TODAY, 4) == 4  # повтор в тот же день не накручивает


# ── Сервис ───────────────────────────────────────────────────────────────────
def test_award_and_idempotent(game):
    t = _task(priority=Priority.high, deadline=TODAY)
    lines = game.complete(t, today=TODAY)
    assert lines and "XP" in lines[0]
    p = game.profile_for("danya_skiba")
    assert p.xp == BASE_XP + 15 + 10 and p.tasks_done == 1

    # повторное закрытие той же задачи не накручивает очки
    assert game.complete(t, today=TODAY) == []
    assert game.profile_for("danya_skiba").xp == BASE_XP + 15 + 10


def test_first_blood_achievement(game):
    game.complete(_task(), today=TODAY)
    p = game.profile_for("danya_skiba")
    assert "first_blood" in p.achievements


def test_alias_resolves_to_same_profile(game):
    # «энди» — алиас Андрея: очки идут в один профиль
    game.complete(_task(assignee="энди"), today=TODAY)
    game.complete(_task(title="Вторая", assignee="andrey"), today=TODAY)
    p = game.profile_for("andrey")
    assert p.tasks_done == 2


def test_multiple_assignees_each_get_xp(game):
    game.complete(_task(assignee="danya_skiba, andrey"), today=TODAY)
    assert game.profile_for("danya_skiba").tasks_done == 1
    assert game.profile_for("andrey").tasks_done == 1


def test_leaderboard_sorted(game):
    game.complete(_task(assignee="danya_skiba", priority=Priority.high, deadline=TODAY), today=TODAY)
    game.complete(_task(assignee="andrey", priority=Priority.low), today=TODAY)
    board = game.leaderboard()
    assert [p.key for p in board] == ["danya_skiba", "andrey"]


def test_level_up_flag(game):
    # копим >50 XP → переход на 2-й уровень должен подсветиться хотя бы раз
    leveled = False
    for i in range(6):
        t = _task(title=f"T{i}", priority=Priority.high, deadline=TODAY)
        lines = game.complete(t, today=TODAY)
        if lines and "уровень" in lines[0]:
            leveled = True
    assert leveled


def test_persistence_roundtrip(tmp_path):
    team = TeamRegistry()
    team.register(TeamMember(user_id=1, username="danya_skiba", full_name="Данила"))
    path = str(tmp_path / "g.json")
    g1 = GamificationService(GameStore(path), team)
    g1.complete(_task(), today=TODAY)
    # новый сервис читает тот же файл
    g2 = GamificationService(GameStore(path), team)
    assert g2.profile_for("danya_skiba").tasks_done == 1
