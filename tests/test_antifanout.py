"""Анти-галлюцинация: фан-аут одной фразы в 15 карточек больше не проходит.

Воспроизводит реальный баг: встреча с 2 задачами, а LLM вернул их + 13
фантомных «Участвовать в демо» на всю команду.
"""

from __future__ import annotations

from datetime import date

import pytest

from dirizher.container import AppContainer
from dirizher.domain.enums import TaskSource
from dirizher.domain.models import ExtractedTask, SourceRef, TeamMember
from dirizher.services.task_service import Outcome

TODAY = date(2026, 6, 7)


class FanoutProvider:
    """LLM-заглушка, имитирующая галлюцинацию фан-аута."""

    name = "fake"

    def __init__(self, extracted: list[ExtractedTask]) -> None:
        self._extracted = extracted

    async def extract_tasks(self, message, context):
        return self._extracted


@pytest.fixture
def c():
    cont = AppContainer()
    cont.team.register(TeamMember(user_id=1, username="danya_skiba", full_name="Данила Скиба", aliases=["Данила"]))
    cont.team.register(TeamMember(user_id=2, username="Stefan_Richards", full_name="Stefan", aliases=["Стефан"]))
    cont.team.register(TeamMember(user_id=3, username="vaiforic", full_name="Вай"))
    cont.team.register(TeamMember(user_id=4, username="andrey", full_name="Андрей"))
    return cont


def _src():
    return SourceRef(source=TaskSource.meeting, chat_id=555)


async def test_fanout_meeting_collapses_to_real_tasks(c):
    transcript = (
        "Данила, тебе нужно до завтра найти библиотеку на питоне и интегрировать в наш сайт. "
        "Андрей, тебе нужно написать страничку для сайта с карточками товара."
    )
    # 2 реальные задачи (исполнители названы) + фан-аут «демо» на тех, кого в тексте НЕТ,
    # и повторы на названного danya.
    extracted = [
        ExtractedTask(task="Интегрировать библиотеку в сайт", assignee="Данила", confidence=0.9),
        ExtractedTask(task="Написать страничку с карточками товара", assignee="Андрей", confidence=0.9),
        ExtractedTask(task="Участвовать в демо", assignee="Stefan_Richards", confidence=0.9),
        ExtractedTask(task="Участвовать в демо", assignee="vaiforic", confidence=0.9),
        ExtractedTask(task="Участвовать в демо", assignee="Данила", confidence=0.9),
        ExtractedTask(task="Участвовать в демо", assignee="Данила", confidence=0.9),
    ]
    c.service.provider = FanoutProvider(extracted)

    processed = await c.service.ingest(transcript, _src(), today=TODAY)
    news = [p for p in processed if p.outcome is Outcome.new]
    titles = sorted(p.task.title for p in news)

    # Остались только 2 реальные задачи; «демо» отфильтровано (исполнители не названы,
    # а повторы на Данилу схлопнуты — но «демо» Даниле не поручали → grounding по исполнителю
    # пропускает только названных, а сама фраза про демо на danya дедупится в одну).
    assert "Интегрировать библиотеку в сайт" in titles
    assert "Написать страничку с карточками товара" in titles
    # Не должно быть 15 карточек
    assert len(news) <= 3
    # Никаких карточек на не упомянутых в тексте Stefan/vaiforic
    assert all((p.task.assignee or "") not in ("Stefan_Richards", "vaiforic") for p in news)


async def test_grounded_assignee_kept(c):
    # исполнитель назван прямо — задача с ним остаётся
    p = await c.service.ingest("Данила, сделай отчёт к пятнице", _src(), today=TODAY)
    news = [x for x in p if x.outcome is Outcome.new]
    assert news and news[0].task.assignee == "danya_skiba"


def test_dedup_batch_collapses_same_title_same_assignee(c):
    from dirizher.domain.models import Task
    from dirizher.services.task_service import ProcessedTask

    items = [
        ProcessedTask(Task(title="Демо", assignee="a"), Outcome.new),
        ProcessedTask(Task(title="Демо", assignee="a"), Outcome.new),
        ProcessedTask(Task(title="Демо", assignee="b"), Outcome.new),
    ]
    out = c.service._dedup_batch(items)
    assert len(out) == 2  # (Демо,a) схлопнут, (Демо,b) отдельный
