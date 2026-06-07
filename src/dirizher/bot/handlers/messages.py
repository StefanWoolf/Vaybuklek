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
from ...logging_setup import get_logger
from ..flow import present

router = Router(name="messages")
log = get_logger("dirizher.bot.messages")

# Явные команды «зафиксируй задачу» — собираем задачу из контекста переписки.
TASK_COMMANDS = (
    "поставь таск", "поставь задач", "сделай задач", "сделай таск", "заведи задач",
    "запиши задач", "запиши это", "зафиксируй", "оформи задач", "добавь задач",
    "создай задач", "поставь на доск", "таск поставь",
)


def _author(user) -> str:
    if user is None:
        return "—"
    return user.full_name or (f"@{user.username}" if user.username else "участник")


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, c: AppContainer, state: FSMContext) -> None:
    # Если пользователь сейчас правит задачу — это сообщение обработает edit-роутер
    if await state.get_state() is not None:
        return

    user = message.from_user
    if user:
        c.team.register(
            TeamMember(user_id=user.id, username=user.username, full_name=user.full_name)
        )

    text = message.text or ""
    chat_id = message.chat.id
    # пополняем историю чата ДО извлечения (сообщение войдёт в контекст)
    c.history.add(chat_id, _author(user), text)

    source = SourceRef(
        source=TaskSource.chat,
        chat_id=chat_id,
        message_id=message.message_id,
        excerpt=text[:200],
    )
    history = c.history.recent(chat_id, limit=12)
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
