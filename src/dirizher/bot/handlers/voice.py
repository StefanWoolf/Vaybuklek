"""Голосовые сообщения и видео-кружки Telegram.

Скачиваем медиа → распознаём речь → распознанный текст уходит в ТОТ ЖЕ конвейер
извлечения задач, что и обычные текстовые сообщения (c.service.ingest). То есть
для бота голосовое/кружок неотличимы от текста — задачи, исполнители и сроки
извлекаются одинаково.

В mock-режиме (DIRIZHER_AUDIO__ENABLED=false) бот честно сообщает, что
распознавание выключено, и предлагает продублировать текстом.
"""

from __future__ import annotations

import tempfile
from html import escape as esc
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from ...container import AppContainer
from ...domain.enums import TaskSource
from ...domain.models import SourceRef, TeamMember
from ...logging_setup import get_logger
from .. import keyboards as kb
from .. import text as tx
from ..flow import present
from ..states import EditTask

router = Router(name="voice")
log = get_logger("dirizher.bot.voice")


def _author(user) -> str:
    if user is None:
        return "—"
    return user.full_name or (f"@{user.username}" if user.username else "участник")


def _media_and_suffix(message: Message):
    """Достаём медиа и расширение файла: голосовое, кружок или аудиофайл."""
    if message.voice:
        return message.voice, ".oga"  # Telegram voice — OGG/Opus
    if message.video_note:
        return message.video_note, ".mp4"  # кружок
    if message.audio:
        suffix = Path(message.audio.file_name or "").suffix or ".ogg"
        return message.audio, suffix
    return None, ""


async def _download(message: Message, file_id: str, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(prefix="dirizher_tg_", suffix=suffix, delete=False) as tmp:
        path = tmp.name
    file = await message.bot.get_file(file_id)
    await message.bot.download_file(file.file_path, destination=path)
    return path


@router.message(F.voice | F.video_note | F.audio)
async def on_voice(message: Message, c: AppContainer, state: FSMContext) -> None:
    if c.transcriber.name == "mock":
        await message.answer(
            "🎙️ Распознавание речи сейчас выключено. Продублируйте задачу текстом — "
            "я её разберу.\n\nЧтобы включить, в <code>.env</code>:\n"
            "<code>DIRIZHER_AUDIO__ENABLED=true</code> (Groq Whisper переиспользует ключи LLM)."
        )
        return

    # Автор голосового — известный участник (чтобы вешать на него задачи).
    user = message.from_user
    if user:
        known_before = c.team.knows(user.id)
        c.team.register(TeamMember(user_id=user.id, username=user.username, full_name=user.full_name))
        if not known_before:
            c.persist()

    media, suffix = _media_and_suffix(message)
    if media is None:
        return

    path = ""
    try:
        path = await _download(message, media.file_id, suffix)
        result = await c.transcriber.transcribe(path)
        text = result.text.strip()
        if not text:
            return  # не расслышал — молчим, как на пустое текстовое сообщение

        # Если идёт правка задачи — голосовое считаем уточнением (явный диалог,
        # тут уместно показать, что услышали).
        if await state.get_state() == EditTask.waiting_correction.state:
            data = await state.get_data()
            await state.clear()
            pending = c.pending.get(data.get("pid", ""))
            if pending:
                await c.service.apply_correction(pending.task, text)
                await message.answer(
                    f"🎙️ Услышал: «{esc(text)}»\n\n"
                    + tx.render_task_card(pending.task, header="✏️ Поправленная задача"),
                    reply_markup=kb.confirm_keyboard(pending.pid),
                )
                return

        # Обычный поток: ведём себя как с текстом — молча извлекаем задачу.
        # Карточка появится через present() только если задача нашлась; иначе тишина.
        chat_id = message.chat.id
        c.history.add(chat_id, _author(user), text)
        source = SourceRef(
            source=TaskSource.voice,
            chat_id=chat_id,
            message_id=message.message_id,
            excerpt=text[:200],
        )
        processed = await c.service.ingest(text, source, history=c.history.recent(chat_id, limit=12))
        if processed:
            await present(message.bot, c, processed, chat_id)
    except Exception:  # noqa: BLE001
        log.exception("Ошибка при обработке голосового/кружка")  # в чат не спамим
    finally:
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
