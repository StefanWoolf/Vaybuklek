"""Знакомство с командой.

Бот не может сам получить список участников группы (ограничение Telegram),
поэтому знакомится интерактивно: представляется при добавлении в чат и при
входе новых участников, а каждый отмечается кнопкой «Представиться». Это даёт
боту связку имя ↔ @username ↔ user_id для корректного назначения и тегов.
"""

from __future__ import annotations

from html import escape as esc

from aiogram import F, Router
from aiogram.filters import ChatMemberUpdatedFilter, IS_NOT_MEMBER, MEMBER, ADMINISTRATOR
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from ...container import AppContainer
from ...domain.models import TeamMember
from ...logging_setup import get_logger
from .. import keyboards as kb
from .. import text as tx
from ..callback_data import IntroCD

router = Router(name="onboarding")
log = get_logger("dirizher.bot.onboarding")

GREETING = (
    "🎼 <b>Всем привет! Я Дирижёр</b> — ваш AI-проджект-менеджер.\n\n"
    "Я читаю чат, нахожу задачи, веду доску YouGile, напоминаю о сроках и "
    "собираю вечерние отчёты. Работаю в фоне — пишите как обычно.\n\n"
    "Чтобы я мог <b>правильно назначать задачи и тегать вас</b>, познакомимся: "
    "нажмите кнопку ниже (каждый), либо <code>/register Имя; алиасы</code>."
)


def _remember_chat(c: AppContainer, chat_id: int) -> None:
    if not c.settings.telegram.team_chat_id:
        c.settings.telegram.team_chat_id = chat_id


async def _greet(target, c: AppContainer, chat_id: int) -> None:
    _remember_chat(c, chat_id)
    await target.send_message(chat_id, GREETING, reply_markup=kb.introduce_keyboard())


# ── Бота добавили в группу ───────────────────────────────────────────────────
@router.my_chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> (MEMBER | ADMINISTRATOR)))
async def on_added(event: ChatMemberUpdated, c: AppContainer) -> None:
    log.info("Бот добавлен в чат %s", event.chat.id)
    await _greet(event.bot, c, event.chat.id)


# ── В чат зашли новые участники ──────────────────────────────────────────────
@router.message(F.new_chat_members)
async def on_new_members(message: Message, c: AppContainer) -> None:
    me = await message.bot.get_me()
    newcomers = [u for u in message.new_chat_members if not u.is_bot]
    if any(u.id == me.id for u in message.new_chat_members):
        await _greet(message.bot, c, message.chat.id)
        return
    if newcomers:
        names = esc(", ".join(u.full_name for u in newcomers))
        await message.answer(
            f"👋 {names}, добро пожаловать! Представьтесь, чтобы я мог вешать на вас задачи:",
            reply_markup=kb.introduce_keyboard(),
        )


# ── «Представиться» (за себя) ────────────────────────────────────────────────
@router.callback_query(IntroCD.filter(F.action == "self"))
async def on_introduce_self(cb: CallbackQuery, c: AppContainer) -> None:
    u = cb.from_user
    c.team.register(TeamMember(user_id=u.id, username=u.username, full_name=u.full_name))
    handle = f"@{u.username}" if u.username else u.full_name
    await cb.answer(f"Готово, записал: {handle}")
    if isinstance(cb.message, Message):
        await cb.message.answer(
            f"✅ Знаком: <b>{esc(u.full_name)}</b>" + (f" (@{esc(u.username)})" if u.username else "")
        )


# ── «Это я (Имя)» — закрепить неизвестного исполнителя ───────────────────────
@router.callback_query(IntroCD.filter(F.action == "claim"))
async def on_claim(cb: CallbackQuery, callback_data: IntroCD, c: AppContainer) -> None:
    u = cb.from_user
    name = callback_data.name
    # регистрируем нажавшего и добавляем имя-из-задачи как алиас
    member = c.team.register(
        TeamMember(user_id=u.id, username=u.username, full_name=u.full_name, aliases=[name] if name else [])
    )
    handle = member.username or member.full_name

    # перепривязываем открытые задачи с этим именем на known-участника
    repointed = 0
    for t in c.repo.open():
        new_list = [handle if a.lstrip("@").lower() == name.lower() else a for a in t.assignees]
        if new_list != t.assignees:
            # дедуп на случай, если handle уже был в списке
            deduped: list[str] = []
            for a in new_list:
                if not any(a.lower() == d.lower() for d in deduped):
                    deduped.append(a)
            t.assignees = deduped
            t.touch()
            repointed += 1

    # обновляем карточку «в полёте», если она ещё ждёт подтверждения
    pending = c.pending.get(callback_data.pid)
    await cb.answer(f"Закрепил «{name}» за вами")
    if pending is not None and isinstance(cb.message, Message):
        # заменяем именно неизвестное имя на закреплённого участника
        pending.task.assignees = [
            handle if a.lstrip("@").lower() == name.lower() else a
            for a in pending.task.assignees
        ] or [handle]
        await cb.message.edit_text(
            tx.render_task_card(pending.task),
            reply_markup=kb.confirm_keyboard(pending.pid),
        )
    elif isinstance(cb.message, Message):
        suffix = f" Обновил задач: {repointed}." if repointed else ""
        await cb.message.answer(f"✅ «{esc(name)}» — это {esc(handle)}.{suffix}")
