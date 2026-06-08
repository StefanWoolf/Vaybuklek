"""Встречи: запись системного звука по ссылке Телемоста + регистрация голоса.

Поток (всё автоматизировано):
1. Кто-то кидает в чат ссылку telemost.yandex.ru/... → бот сам начинает писать
   СИСТЕМНЫЙ звук машины (loopback). Бот не «входит» в конфу — слушает звук ПК,
   который уже в звонке (захват с драйвера, как в кейсе).
2. Запись сама останавливается по долгой тишине или по `/meeting_stop`.
3. Запись → Whisper + диаризация + авто-имена по голосу → саммари + задачи на доску.

`/enroll_voice` — записать голосовой отпечаток участника, чтобы на встречах
Speaker_1 заменялся реальным именем.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from html import escape as esc
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from ...container import AppContainer
from ...logging_setup import get_logger
from ..flow import present
from ..states import EnrollVoice

router = Router(name="meeting")
log = get_logger("dirizher.bot.meeting")

# Ссылки Яндекс.Телемоста (и telemost.yandex.* доменов)
_TELEMOST_RE = re.compile(r"https?://telemost\.yandex\.[a-z]+/\S+", re.IGNORECASE)


def has_telemost_link(text: str) -> bool:
    return bool(_TELEMOST_RE.search(text or ""))


async def _process_recording(c: AppContainer, bot, chat_id: int, path: str | None, reason: str) -> None:
    """Колбэк по завершении записи: распознать, выделить задачи, отчитаться."""
    c.active_meetings.pop(chat_id, None)
    why = {"silence": "тишина", "timeout": "лимит времени", "manual": "по команде"}.get(reason, reason)
    if not path:
        await bot.send_message(chat_id, f"⏹️ Запись встречи остановлена ({why}) — звука не было.")
        return
    await bot.send_message(chat_id, f"⏹️ Запись остановлена ({why}). Распознаю встречу…")
    try:
        transcript = await c.transcriber.transcribe(path)
    except Exception as e:  # noqa: BLE001
        log.exception("Сбой распознавания встречи")
        await bot.send_message(chat_id, f"Не смог распознать встречу 😕\n<code>{esc(str(e))}</code>")
        return
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    if transcript.is_mock or not transcript.text.strip():
        await bot.send_message(chat_id, "Речь на встрече не распознана.")
        return

    result = await c.meeting.process(transcript, chat_id=chat_id)
    await bot.send_message(chat_id, "📝 <b>Саммари встречи</b>\n" + esc(result.summary))

    if not result.processed:
        await bot.send_message(chat_id, "Задач из встречи не выделил.")
        return
    # Те же кнопки подтверждения/правки, что и для задач из чата: в ручном режиме
    # каждую задачу можно подтвердить, поправить (срок/исполнителя) или отклонить;
    # в авто-режиме (/mode auto) — заводятся сразу.
    await bot.send_message(
        chat_id, f"📋 Нашёл задач из встречи: <b>{len(result.processed)}</b> — проверьте:"
    )
    await present(bot, c, result.processed, chat_id)


@router.message(F.text.func(lambda t: has_telemost_link(t)))
async def on_telemost_link(message: Message, c: AppContainer) -> None:
    chat_id = message.chat.id
    if c.transcriber.name == "mock":
        await message.answer(
            "🔗 Вижу ссылку на встречу, но распознавание выключено "
            "(<code>DIRIZHER_AUDIO__ENABLED=false</code>) — запись не веду."
        )
        return
    if chat_id in c.active_meetings:
        await message.answer("🔴 Уже пишу эту встречу. Остановить — /meeting_stop.")
        return

    from ...audio.recorder import MeetingRecorder, loopback_available

    if not loopback_available():
        await message.answer(
            "🎧 Чтобы писать встречу, нужен захват системного звука. Установите:\n"
            "<code>pip install soundcard</code> и перезапустите бота."
        )
        return

    loop = asyncio.get_running_loop()
    bot = message.bot

    async def on_finish(path: str | None, reason: str) -> None:
        await _process_recording(c, bot, chat_id, path, reason)

    rec = MeetingRecorder(c.settings.audio, on_finish, loop)
    if not rec.start():
        await message.answer("Не нашёл устройство для захвата звука 😕 Проверьте колонки/драйвер.")
        return
    c.active_meetings[chat_id] = rec
    mins = c.settings.audio.meeting_silence_seconds // 60
    await message.answer(
        "🔴 <b>Пишу встречу</b> (системный звук).\n"
        f"Остановлю сам после тишины (~{mins} мин) или командой /meeting_stop.\n"
        "Когда закончу — пришлю саммари и вынесу задачи на доску."
    )


@router.message(Command("meeting_stop"))
async def cmd_meeting_stop(message: Message, c: AppContainer) -> None:
    rec = c.active_meetings.get(message.chat.id)
    if rec is None:
        await message.answer("Сейчас запись встречи не идёт.")
        return
    rec.stop("manual")  # обработку и ответ даст колбэк on_finish
    await message.answer("⏹️ Останавливаю запись, обрабатываю…")


# ── Регистрация голосового отпечатка ─────────────────────────────────────────
@router.message(Command("enroll_voice"))
async def cmd_enroll_voice(message: Message, c: AppContainer, state: FSMContext) -> None:
    if c.embedder is None:
        await message.answer(
            "🎙️ Авто-имена по голосу доступны при включённом распознавании:\n"
            "<code>DIRIZHER_AUDIO__ENABLED=true</code> в <code>.env</code>."
        )
        return
    await state.set_state(EnrollVoice.waiting_voice)
    await message.answer("🎙️ Пришлите короткое голосовое (5–10 сек) — запомню ваш голос для встреч.")


@router.message(EnrollVoice.waiting_voice, F.voice | F.audio)
async def on_enroll_voice(message: Message, c: AppContainer, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    member = c.team.resolve(user.username or user.full_name) if user else None
    name = (member.full_name if member else None) or (user.full_name if user else "Участник")

    media = message.voice or message.audio
    suffix = ".oga" if message.voice else ".ogg"
    with tempfile.NamedTemporaryFile(prefix="dirizher_enroll_", suffix=suffix, delete=False) as tmp:
        path = tmp.name
    try:
        file = await message.bot.get_file(media.file_id)
        await message.bot.download_file(file.file_path, destination=path)
        emb = await asyncio.to_thread(c.embedder.embed_file, path)
        c.speakers.enroll(name, emb)
        await message.answer(f"✅ Запомнил голос: <b>{esc(name)}</b>. На встречах подпишу ваши реплики.")
    except Exception as e:  # noqa: BLE001
        log.exception("Сбой регистрации голоса")
        await message.answer(f"Не смог запомнить голос 😕\n<code>{esc(str(e))}</code>")
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
