"""Знакомство с командой.

Бот не может сам получить список участников группы (ограничение Telegram),
поэтому знакомится интерактивно: представляется при добавлении в чат и при
входе новых участников, а каждый отмечается кнопкой «Представиться». Это даёт
боту связку имя ↔ @username ↔ user_id для корректного назначения и тегов.
"""

from __future__ import annotations

import re
from html import escape as esc

from aiogram import F, Router
from aiogram.filters import ChatMemberUpdatedFilter, IS_NOT_MEMBER, MEMBER, ADMINISTRATOR
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from ...container import AppContainer
from ...domain.models import TeamMember
from ...logging_setup import get_logger
from .. import keyboards as kb
from .. import text as tx
from ..callback_data import IntroCD
from ..states import Introduce

router = Router(name="onboarding")
log = get_logger("dirizher.bot.onboarding")

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

ASK_DETAILS = (
    "Давай знакомиться 👋\n"
    "Пришли одним сообщением <b>email, привязанный к доске YouGile</b>, и через "
    "запятую свои <b>прозвища</b> (как тебя зовут в чате).\n\n"
    "Пример: <code>andrey@mail.ru; Энди, Андрюша, Андрей Скрипа</code>\n"
    "Если прозвищ нет — пришли просто email."
)

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


# ── «Представиться» (за себя) → спрашиваем email и прозвища ──────────────────
@router.callback_query(IntroCD.filter(F.action == "self"))
async def on_introduce_self(cb: CallbackQuery, c: AppContainer, state: FSMContext) -> None:
    u = cb.from_user
    # базовую личность фиксируем сразу, детали (email/прозвища) — следующим сообщением
    c.team.register(TeamMember(user_id=u.id, username=u.username, full_name=u.full_name))
    await state.set_state(Introduce.waiting_details)
    await cb.answer()
    if isinstance(cb.message, Message):
        await cb.message.answer(ASK_DETAILS)


def _parse_details(text: str) -> tuple[str | None, list[str]]:
    """Из «email; Энди, Андрюша» вернуть (email, [прозвища])."""
    em = _EMAIL_RE.search(text)
    email = em.group(0) if em else None
    rest = (text[: em.start()] + " " + text[em.end():]) if em else text
    parts = [a.strip(" \t.;") for a in re.split(r"[;,\n]", rest)]
    aliases = [a for a in parts if a and "@" not in a]
    return email, aliases


@router.message(Introduce.waiting_details)
async def on_details(message: Message, c: AppContainer, state: FSMContext) -> None:
    u = message.from_user
    email, aliases = _parse_details(message.text or "")
    if not email:
        await message.answer("Не вижу email 🤔 Пришли адрес, привязанный к доске YouGile.")
        return  # остаёмся в состоянии, ждём корректный ввод

    await state.clear()
    member = c.team.register(
        TeamMember(user_id=u.id, username=u.username, full_name=u.full_name,
                   aliases=aliases, email=email)
    )

    # Привязка к реальному пользователю доски YouGile по email
    bound = ""
    try:
        found = await c.board.find_user_by_email(email)
    except Exception:  # noqa: BLE001
        found = None
    if found:
        member.yougile_id, yg_name = found
        bound = f"\n🔗 Привязал к пользователю доски: <b>{esc(yg_name)}</b>"
    elif not c.settings.yougile.is_mock:
        bound = ("\n⚠️ На доске нет пользователя с таким email — задачи будут "
                 "назначаться по имени. Проверь адрес или пригласи его на доску.")

    # перепривязываем уже открытые задачи, где исполнитель совпал с прозвищем
    handle = member.username or member.full_name
    repointed = 0
    keys = {a.lower() for a in aliases} | {(member.full_name or "").lower()}
    for t in c.repo.open():
        if t.assignee and t.assignee.lstrip("@").lower() in keys:
            t.assignee = handle
            if member.yougile_id:
                t.assignee_yougile_id = member.yougile_id
            t.touch()
            repointed += 1

    alias_str = ", ".join(aliases) if aliases else "—"
    suffix = f"\nПереназначил задач: {repointed}." if repointed else ""
    await message.answer(
        f"✅ Знаком: <b>{esc(member.full_name)}</b>"
        + (f" (@{esc(member.username)})" if member.username else "")
        + f"\n📧 {esc(email)}\n🏷️ Прозвища: {esc(alias_str)}{bound}{suffix}"
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
        if t.assignee and t.assignee.lstrip("@").lower() == name.lower():
            t.assignee = handle
            t.touch()
            repointed += 1

    # обновляем карточку «в полёте», если она ещё ждёт подтверждения
    pending = c.pending.get(callback_data.pid)
    await cb.answer(f"Закрепил «{name}» за вами")
    if pending is not None and isinstance(cb.message, Message):
        pending.task.assignee = handle
        await cb.message.edit_text(
            tx.render_task_card(pending.task),
            reply_markup=kb.confirm_keyboard(pending.pid),
        )
    elif isinstance(cb.message, Message):
        suffix = f" Обновил задач: {repointed}." if repointed else ""
        await cb.message.answer(f"✅ «{esc(name)}» — это {esc(handle)}.{suffix}")
