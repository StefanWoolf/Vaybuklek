"""Сверка памяти с доской: «призраки» (удалённые на доске карточки) уходят."""

from __future__ import annotations

import pytest

from dirizher.container import AppContainer
from dirizher.domain.enums import TaskSource
from dirizher.domain.models import SourceRef, Task
from dirizher.integrations.yougile import BoardCard


class FakeBoard:
    """Доска, возвращающая только заданные карточки (как живой YouGile)."""

    name = "yougile"

    def __init__(self, live_ids: list[str]) -> None:
        self._live = live_ids

    async def list_cards(self) -> list[BoardCard]:
        return [BoardCard(id=i, title=f"card {i}") for i in self._live]


@pytest.fixture
def c():
    return AppContainer()


def _seed(c, *card_ids):
    c.repo._tasks.clear()
    for cid in card_ids:
        t = Task(title=f"T{cid}", board_card_id=cid,
                 sources=[SourceRef(source=TaskSource.chat, chat_id=1)])
        c.repo.add(t)


async def test_prunes_ghosts(c):
    _seed(c, "a", "b", "ghost1", "ghost2")
    c.service.board = FakeBoard(["a", "b"])  # ghost1/ghost2 удалены на доске
    removed = await c.service.reconcile_with_board()
    assert removed == 2
    titles = {t.title for t in c.repo.all()}
    assert titles == {"Ta", "Tb"}


async def test_keeps_all_when_board_matches(c):
    _seed(c, "a", "b")
    c.service.board = FakeBoard(["a", "b"])
    assert await c.service.reconcile_with_board() == 0
    assert len(c.repo.all()) == 2


async def test_guard_skips_on_empty_board(c):
    """Доска вернула пусто при синхронизированных задачах → вероятный сбой, не трём."""
    _seed(c, "a", "b")
    c.service.board = FakeBoard([])
    assert await c.service.reconcile_with_board() == 0
    assert len(c.repo.all()) == 2  # ничего не удалили


async def test_mock_board_is_noop(c):
    _seed(c, "a")
    # дефолтная доска контейнера — mock; сверка должна быть безопасным no-op
    assert await c.service.reconcile_with_board() == 0
    assert len(c.repo.all()) == 1
