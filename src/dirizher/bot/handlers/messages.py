"""Обработка обычных текстовых сообщений: извлечение задач из переписки.

Ведём скользящее окно сообщений чата и передаём его в LLM как контекст —
чтобы бот понимал ссылки и команды «поставь таску» по обсуждению выше.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from ...container import AppContainer
from ...domain.enums import TaskSource
from ...domain.models import SourceRef, TeamMember
from ...llm.prefilter import looks_taskish
from ...logging_setup import get_logger
import re

from .. import task_commands
from ..flow import present
from .commands import send_my_tasks

router = Router(name="messages")
log = get_logger("dirizher.bot.messages")

# Явные команды «зафиксируй задачу» — собираем задачу из контекста переписки.
TASK_COMMANDS = (
    "поставь таск", "поставь задач", "сделай задач", "сделай таск", "заведи задач",
    "запиши задач", "запиши это", "зафиксируй", "оформи задач", "добавь задач",
    "создай задач", "поставь на доск", "таск поставь",
)


# «Какие у меня задачи/таски?», «мои задачи», «что по моим таскам» — показать список
_MY_TASKS_RE = re.compile(
    r"(?:какие|что|покажи|мои|моих|моим|список)\b.{0,20}\b(?:задач|таск|дел)",
    re.IGNORECASE,
)
# Короткая форма-запрос: «таски», «задачи @username», «мои дела»
_TASKS_LEAD_RE = re.compile(
    r"^\s*(?:мои\s+)?(?:задач\w*|таск\w*|дела)\b(?:\s+@?\w+)?\s*[?.!]*$",
    re.IGNORECASE,
)


def _is_my_tasks_query(text: str) -> bool:
    low = text.lower()
    return bool(_MY_TASKS_RE.search(low) or _TASKS_LEAD_RE.match(low))


def _author(user) -> str:
    if user is None:
        return "—"
    return user.full_name or (f"@{user.username}" if user.username else "участник")


def _team_names(c: AppContainer) -> tuple[str, ...]:
    """Имена/алиасы/username команды — для предфильтра (поручение участнику)."""
    names: list[str] = []
    for m in c.team.all():
        if m.username:
            names.append(m.username)
        if m.full_name:
            names.append(m.full_name.split()[0])
        names.extend(m.aliases)
    return tuple(names)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, c: AppContainer, state: FSMContext) -> None:
    # Если пользователь сейчас правит задачу — это сообщение обработает edit-роутер
    if await state.get_state() is not None:
        return

    user = message.from_user
    if user:
        known_before = c.team.knows(user.id)
        c.team.register(
            TeamMember(user_id=user.id, username=user.username, full_name=user.full_name)
        )
        if not known_before:
            c.persist()  # новый участник — сохраняем, чтобы пережил перезапуск

    text = message.text or ""
    chat_id = message.chat.id
    # пополняем историю чата ДО извлечения (сообщение войдёт в контекст)
    c.history.add(chat_id, _author(user), text)

    # Вопрос «какие у меня задачи?» / «таски @user» — интерактивный список
    if _is_my_tasks_query(text):
        await send_my_tasks(message, c, query_text=text)
        return

    # Команда над существующей задачей (закрой/в работу/удали) — раньше извлечения
    cmd = task_commands.detect(text)
    if cmd is not None:
        await task_commands.handle(message, c, cmd)
        return

    # Предфильтр: на заведомый мусор (приветствия/реакции/короткие вопросы) не
    # тратим вызов LLM. При любом намёке на задачу — пропускаем дальше.
    if not looks_taskish(text, _team_names(c)):
        return

    source = SourceRef(
        source=TaskSource.chat,
        chat_id=chat_id,
        message_id=message.message_id,
        excerpt=text[:200],
    )
    history = c.history.recent(chat_id, limit=10)
    processed = await c.service.ingest(text, source, history=history)

    if processed:
        await present(message.bot, c, processed, chat_id)
        return

    # Явная команда «поставь таску», но из контекста ничего не собралось —
    # не молчим, а просим уточнить (иначе выглядит, будто бот игнорит).
    if any(cmd in text.lower() for cmd in TASK_COMMANDS):
        await message.answer(
            "🤔 Не понял, какую задачу зафиксировать. Опишите коротко: "
            "что сделать, кто исполнитель и срок — или повторите одним сообщением."
        )
    # Иначе тишина — не засоряем чат на каждое сообщение.
