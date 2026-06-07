"""Голосовые сообщения и видео-кружки: распознавание → задачи (усиление, Этап 3).

В mock-режиме (DIRIZHER_AUDIO__ENABLED=false) бот честно сообщает, что
распознавание выключено, и предлагает продублировать текстом.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from ...container import AppContainer
from ...domain.enums import TaskSource
from ...domain.models import SourceRef
from ...logging_setup import get_logger
from .. import keyboards as kb
from .. import text as tx
from ..flow import present
from ..states import EditTask

router = Router(name="voice")
log = get_logger("dirizher.bot.voice")


async def _download(message: Message, file_id: str, suffix: str) -> str:
    tmp = Path(tempfile.gettempdir()) / f"dirizher_{file_id}{suffix}"
    file = await message.bot.get_file(file_id)
    await message.bot.download_file(file.file_path, destination=str(tmp))
    return str(tmp)


@router.message(F.voice | F.video_note)
async def on_voice(message: Message, c: AppContainer, state: FSMContext) -> None:
    if c.transcriber.name == "mock":
        await message.answer(
            "🎙️ Распознавание речи — усиление (Этап 3) и сейчас выключено "
            "(`DIRIZHER_AUDIO__ENABLED=false`). Продублируйте задачу текстом — "
            "я её разберу.",
            parse_mode="Markdown",
        )
        return

    media = message.voice or message.video_note
    suffix = ".ogg" if message.voice else ".mp4"
    path = await _download(message, media.file_id, suffix)
    result = await c.transcriber.transcribe(path)
    if not result.text.strip():
        await message.answer("Не удалось распознать речь 😕")
        return

    # Если идёт правка задачи — применяем как уточнение
    if await state.get_state() == EditTask.waiting_correction.state:
        data = await state.get_data()
        await state.clear()
        pending = c.pending.get(data.get("pid", ""))
        if pending:
            await c.service.apply_correction(pending.task, result.text)
            await message.answer(
                f"🎙️ Услышал: «{result.text}»\n\n"
                + tx.render_task_card(pending.task, header="✏️ Поправленная задача"),
                reply_markup=kb.confirm_keyboard(pending.pid),
                parse_mode="Markdown",
            )
            return

    await message.answer(f"🎙️ Распознал: «{result.text}»")
    author = message.from_user.full_name if message.from_user else "—"
    c.history.add(message.chat.id, author, result.text)
    source = SourceRef(
        source=TaskSource.voice,
        chat_id=message.chat.id,
        message_id=message.message_id,
        excerpt=result.text[:200],
    )
    processed = await c.service.ingest(
        result.text, source, history=c.history.recent(message.chat.id, limit=12)
    )
    await present(message.bot, c, processed, message.chat.id)
