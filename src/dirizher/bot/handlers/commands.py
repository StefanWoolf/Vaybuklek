"""Команды бота: /start, /help, /mode, /board, /tasks, /register, /whoami."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ...container import AppContainer
from ...domain.models import TeamMember
from .. import keyboards as kb
from .. import text as tx

router = Router(name="commands")

HELP = """\
🎼 *Дирижёр* — автономный AI-проджект-менеджер.

Я читаю чат, извлекаю задачи, веду доску YouGile, напоминаю о сроках и
сверяю вечерние отчёты. Просто пишите в чат как обычно — задачи я найду сам.

*Команды*
/mode — режим отправки задач: авто / с подтверждением
/board — показать канбан-доску
/tasks — мои открытые задачи
/report <текст> — вечерний отчёт (бот сам проставит статусы)
/reconcile — показать вечернюю сверку сейчас
/remind — проверить дедлайны и напомнить
/join — представиться (чтобы я мог вешать на вас задачи)
/register Имя; алиас1, алиас2 — представиться с алиасами
/whoami — как я вас вижу
/help — эта справка

Совет: нажмите «👋 Представиться» — так я свяжу ваше имя с аккаунтом.
"""


@router.message(Command("start", "help", "join"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, reply_markup=kb.introduce_keyboard(), parse_mode="Markdown")


@router.message(Command("mode"))
async def cmd_mode(message: Message, command: CommandObject, c: AppContainer) -> None:
    chat_id = message.chat.id
    arg = (command.args or "").strip().lower()
    if arg in {"auto", "авто", "on", "true", "1"}:
        c.mode.set_auto(chat_id, True)
    elif arg in {"manual", "ручной", "off", "false", "0", "confirm"}:
        c.mode.set_auto(chat_id, False)
    state = "АВТО (без подтверждений)" if c.mode.is_auto(chat_id) else "С ПОДТВЕРЖДЕНИЕМ"
    await message.answer(
        f"⚙️ Режим отправки задач: *{state}*\n\n"
        f"`/mode auto` — отправлять сразу\n"
        f"`/mode manual` — спрашивать подтверждение и предлагать правку",
        parse_mode="Markdown",
    )


@router.message(Command("board"))
async def cmd_board(message: Message, c: AppContainer) -> None:
    cards = await c.board.list_cards()
    await message.answer(tx.render_board(cards), parse_mode="Markdown")


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, c: AppContainer) -> None:
    user = message.from_user
    name = user.username or (user.full_name if user else "")
    tasks = c.repo.open_by_assignee(name) if name else []
    if not tasks:
        await message.answer("У вас нет открытых задач 🎉")
        return
    lines = ["*Ваши открытые задачи:*", ""]
    for t in tasks:
        lines.append(tx.render_task_card(t, header=f"📋 {t.title}").split("\n", 1)[1])
        lines.append("")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("register"))
async def cmd_register(message: Message, command: CommandObject, c: AppContainer) -> None:
    user = message.from_user
    args = (command.args or "").strip()
    full_name = user.full_name
    aliases: list[str] = []
    if args:
        parts = args.split(";", 1)
        full_name = parts[0].strip() or full_name
        if len(parts) > 1:
            aliases = [a.strip() for a in parts[1].split(",") if a.strip()]
    c.team.register(
        TeamMember(
            user_id=user.id,
            username=user.username,
            full_name=full_name,
            aliases=aliases,
        )
    )
    await message.answer(
        f"✅ Записал: *{full_name}*"
        + (f" (@{user.username})" if user.username else "")
        + (f"\nАлиасы: {', '.join(aliases)}" if aliases else ""),
        parse_mode="Markdown",
    )


@router.message(Command("report"))
async def cmd_report(message: Message, command: CommandObject, c: AppContainer) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "Напишите отчёт после команды, например:\n"
            "`/report авторизацию сделал, макет ещё в работе`",
            parse_mode="Markdown",
        )
        return
    user = message.from_user
    key = user.username or user.full_name
    notes = await c.reconciliation.record_report(message.chat.id, key, text)
    if notes:
        await message.answer("Принял отчёт, обновил доску:\n" + "\n".join(notes))
    else:
        await message.answer("Принял отчёт ✅ (подходящих открытых задач не нашёл для авто-статуса)")


@router.message(Command("reconcile"))
async def cmd_reconcile(message: Message, c: AppContainer) -> None:
    digest, _ = c.reconciliation.evening_digest(message.chat.id)
    await message.answer(digest, parse_mode="Markdown")


@router.message(Command("remind"))
async def cmd_remind(message: Message, c: AppContainer) -> None:
    from ...scheduler.jobs import run_reminders

    if c.bot is None:
        c.bot = message.bot
    sent = await run_reminders(c)
    if sent == 0:
        await message.answer("Нет задач для напоминания сейчас 👍")


@router.message(Command("whoami"))
async def cmd_whoami(message: Message, c: AppContainer) -> None:
    user = message.from_user
    m = c.team.resolve(user.username or user.full_name)
    if m:
        await message.answer(
            f"Вы: *{m.full_name or '—'}* (@{m.username or '—'})\n"
            f"Алиасы: {', '.join(m.aliases) or '—'}",
            parse_mode="Markdown",
        )
    else:
        await message.answer("Я вас ещё не записал. Используйте /register.")
