"""Голосовые сообщения Telegram: скачивание → распознавание → извлечение задач."""

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


async def _download(message: Message, file_id: str, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(
        prefix="dirizher_tg_audio_",
        suffix=suffix,
        delete=False,
    ) as tmp:
        path = tmp.name

    file = await message.bot.get_file(file_id)
    await message.bot.download_file(file.file_path, destination=path)

    return path


def _get_media_and_suffix(message: Message):
    if message.voice:
        return message.voice, ".oga"

    if message.video_note:
        return message.video_note, ".mp4"

    if message.audio:
        file_name = message.audio.file_name or ""
        suffix = Path(file_name).suffix or ".ogg"
        return message.audio, suffix

    return None, ""


@router.message(F.voice | F.video_note | F.audio)
async def on_voice(message: Message, c: AppContainer, state: FSMContext) -> None:
    if c.transcriber.name == "mock":
        await message.answer(
            "🎙️ Распознавание речи сейчас выключено.\n\n"
            "Включите его в `.env`:\n"
            "<code>DIRIZHER_AUDIO__ENABLED=true</code>\n"
            "<code>DIRIZHER_AUDIO__WHISPER_MODEL=small</code>"
        )
        return

    user = message.from_user

    if user:
        c.team.register(
            TeamMember(
                user_id=user.id,
                username=user.username,
                full_name=user.full_name,
            )
        )

    media, suffix = _get_media_and_suffix(message)

    if media is None:
        await message.answer("Не нашёл аудиофайл в сообщении 😕")
        return

    status = await message.answer("🎙️ Слушаю голосовое...")

    path = ""

    try:
        path = await _download(message, media.file_id, suffix)
        result = await c.transcriber.transcribe(path)

        recognized_text = result.text.strip()

        if not recognized_text:
            await status.edit_text("Не удалось распознать речь 😕")
            return

        # Если пользователь сейчас правит задачу — голосовое считаем уточнением.
        if await state.get_state() == EditTask.waiting_correction.state:
            data = await state.get_data()
            await state.clear()

            pending = c.pending.get(data.get("pid", ""))

            if pending:
                await c.service.apply_correction(pending.task, recognized_text)

                await status.edit_text(
                    f"🎙️ Услышал: «{esc(recognized_text)}»\n\n"
                    + tx.render_task_card(
                        pending.task,
                        header="✏️ Поправленная задача",
                    ),
                    reply_markup=kb.confirm_keyboard(pending.pid),
                )
                return

        await status.edit_text(f"🎙️ Распознал: «{esc(recognized_text)}»")

        chat_id = message.chat.id
        c.history.add(chat_id, _author(user), recognized_text)

        source = SourceRef(
            source=TaskSource.voice,
            chat_id=chat_id,
            message_id=message.message_id,
            excerpt=recognized_text[:200],
        )

        processed = await c.service.ingest(
            recognized_text,
            source,
            history=c.history.recent(chat_id, limit=12),
        )

        if processed:
            await present(message.bot, c, processed, chat_id)
        else:
            await message.answer(
                "Текст распознал, но задачу в нём не нашёл. "
                "Попробуйте сказать конкретнее: что сделать, кто исполнитель и срок."
            )

    except Exception as e:
        log.exception("Ошибка при обработке голосового сообщения")
        await status.edit_text(f"Не смог обработать голосовое 😕\n<code>{esc(str(e))}</code>")

    finally:
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass
