"""Команды бота: /start, /help, /mode, /board, /tasks, /register, /whoami."""

from __future__ import annotations

from html import escape as esc

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ...container import AppContainer
from ...domain.enums import TaskStatus
from ...domain.models import TeamMember
from .. import keyboards as kb
from .. import text as tx

router = Router(name="commands")

HELP = """\
🎼 <b>Дирижёр</b> — автономный AI-проджект-менеджер.

Я читаю чат, извлекаю задачи, веду доску YouGile, напоминаю о сроках и
сверяю вечерние отчёты. Просто пишите в чат как обычно — задачи я найду сам.

<b>Команды</b>
/mode — режим отправки задач: авто / с подтверждением
/board — показать канбан-доску
/tasks — мои открытые задачи
/profile — мой игровой профиль: XP, уровень, ачивки 🎮
/leaderboard — рейтинг команды по очкам 🏆
/report текст — вечерний отчёт (бот сам проставит статусы)
/reconcile — показать вечернюю сверку сейчас
/remind — проверить дедлайны и напомнить
/sync — сверить память с доской (убрать «призраков»)
/join — представиться (чтобы я мог вешать на вас задачи)
/register Имя; алиас1, алиас2 — представиться с алиасами
/alias энди, стеф — заменить свои прозвища (для тёзок)
/whoami — как я вас вижу
/enroll_voice — запомнить мой голос (подпись реплик на встречах)
/meeting_stop — остановить запись встречи
/forget — забыть всех участников (сброс памяти команды)
/help — эта справка

🎤 Кинь в чат ссылку Яндекс.Телемоста — я начну писать звук встречи, а в конце
пришлю саммари и вынесу задачи на доску.

Совет: нажмите «👋 Представиться» — так я свяжу ваше имя с аккаунтом.
"""


@router.message(Command("start", "help", "join"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, reply_markup=kb.introduce_keyboard())


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
        f"⚙️ Режим отправки задач: <b>{state}</b>\n\n"
        f"<code>/mode auto</code> — отправлять сразу\n"
        f"<code>/mode manual</code> — спрашивать подтверждение и предлагать правку"
    )


@router.message(Command("board"))
async def cmd_board(message: Message, c: AppContainer) -> None:
    cards = await c.board.list_cards()
    await message.answer(tx.render_board(cards))


import re as _re

from ...domain.models import TeamMember as _TeamMember

_MENTION_RE = _re.compile(r"@([A-Za-z0-9_]{3,})")


def _card_belongs_to(card, member) -> bool:
    """Карточка назначена на участника? Сначала по id доски (точно), затем по имени."""
    if member is None:
        return False
    # точное совпадение по привязке к пользователю YouGile
    if member.yougile_id and member.yougile_id in getattr(card, "assignee_ids", []):
        return True
    # запасной вариант по имени — пока человек не привязал email к доске
    if card.assignee:
        who = card.assignee.lower()
        cands = [member.username or "", member.full_name, *member.aliases]
        if member.full_name:
            cands.append(member.full_name.split()[0])
        return any(cand and cand.lower() in who for cand in cands)
    return False


def _resolve_target(c: AppContainer, text: str, author) -> tuple[object | None, str, bool]:
    """Чьи задачи показывать: упомянутый @username или сам автор.

    Возвращает (участник|None, подпись, это_я). None — когда цель неизвестна
    (чтобы НЕ показывать чужие задачи всем подряд).
    """
    m = _MENTION_RE.search(text or "")
    if m:
        member = c.team.resolve(m.group(1))
        if member:
            label = f"@{member.username}" if member.username else (member.full_name or m.group(1))
            return member, label, False
        return None, m.group(1), False  # упомянут неизвестный — его задач не знаем
    if author is not None:
        # автор всегда известен (регистрируем на лету), матч по имени/привязке
        member = c.team.register(
            _TeamMember(user_id=author.id, username=author.username, full_name=author.full_name)
        )
        return member, "вами", True
    return None, "", False


async def send_my_tasks(message: Message, c: AppContainer, *, query_text: str = "") -> None:
    """Показать задачи (мои или указанного @username) — по одной с кнопками.

    Источник истины — доска YouGile (переживает перезапуски). Чужие задачи в общий
    чат не вываливаем: если цель не определена — просим уточнить/представиться.
    """
    target, label, is_self = _resolve_target(c, query_text, message.from_user)
    if target is None:
        if label:  # упомянули неизвестного участника
            await message.answer(f"Не знаю участника «{label}» 🤔 Пусть он представится через /start.")
        else:
            await message.answer(
                "Не понял, чьи задачи показать. Представьтесь через /start → «Представиться» "
                "или уточните: <code>таски @username</code>."
            )
        return

    cards = await c.board.list_cards()
    mine = [c_ for c_ in cards if c_.status is not TaskStatus.done and _card_belongs_to(c_, target)]

    whose = "У вас" if is_self else f"У {label}"
    if not mine:
        await message.answer(f"{whose} нет открытых задач 🎉")
        return

    title = "Ваши открытые задачи" if is_self else f"Открытые задачи — {label}"
    await message.answer(f"<b>{title}:</b>")
    for card in mine[:20]:
        await message.answer(
            tx.render_board_task(card),
            reply_markup=kb.board_task_keyboard(card.id, card.status),
        )


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, c: AppContainer) -> None:
    await send_my_tasks(message, c)


@router.message(Command("profile", "me", "profil", "профиль"))
async def cmd_profile(message: Message, command: CommandObject, c: AppContainer) -> None:
    """Игровой профиль: свой или указанного @username (п.10)."""
    user = message.from_user
    arg = (command.args or "").strip()
    if arg:
        target = arg
    elif user:
        # запоминаем автора на лету, чтобы профиль вёлся по стабильному ключу
        c.team.register(
            TeamMember(user_id=user.id, username=user.username, full_name=user.full_name)
        )
        target = user.username or user.full_name
    else:
        target = ""
    await message.answer(c.game.render_profile(target))


@router.message(Command("leaderboard", "top", "leaders", "рейтинг", "топ"))
async def cmd_leaderboard(message: Message, c: AppContainer) -> None:
    await message.answer(c.game.render_leaderboard())


@router.message(Command("game_reset", "leaderboard_reset", "сброс_очков"))
async def cmd_game_reset(message: Message, c: AppContainer) -> None:
    """Обнулить лидерборд (убрать тестовые/устаревшие профили)."""
    n = c.game.reset()
    await message.answer(
        f"🧹 Лидерборд обнулён (удалено профилей: {n}). Очки начнут копиться заново "
        f"по мере закрытия задач."
    )


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
    c.persist()
    await message.answer(
        f"✅ Записал: <b>{esc(full_name)}</b>"
        + (f" (@{esc(user.username)})" if user.username else "")
        + (f"\nАлиасы: {esc(', '.join(aliases))}" if aliases else "")
    )


@router.message(Command("alias", "aliases", "алиас", "алиасы"))
async def cmd_alias(message: Message, command: CommandObject, c: AppContainer) -> None:
    """Заменить свои прозвища (алиасы). Решает коллизию тёзок: «Андрей» у двоих.

    /alias — показать текущие
    /alias энди, стеф — заменить список (через запятую)
    """
    user = message.from_user
    me = c.team.register(
        TeamMember(user_id=user.id, username=user.username, full_name=user.full_name)
    )
    args = (command.args or "").strip()
    if not args:
        cur = ", ".join(me.aliases) or "— нет"
        await message.answer(
            f"Ваши прозвища: <b>{esc(cur)}</b>\n"
            f"Заменить: <code>/alias энди, стеф</code> · убрать все: <code>/alias -</code>"
        )
        return

    new_aliases = [] if args in {"-", "—", "нет"} else [a.strip() for a in args.split(",") if a.strip()]
    me.aliases = new_aliases
    c.persist()

    # Предупреждаем о коллизиях: тот же алиас есть у другого участника
    clashes: list[str] = []
    for a in new_aliases:
        others = [m for m in c.team.resolve_all(a) if m.user_id != user.id]
        if others:
            who = ", ".join("@" + (m.username or m.full_name) for m in others)
            clashes.append(f"«{a}» — также у {who}")
    text = f"✅ Прозвища обновлены: <b>{esc(', '.join(new_aliases) or '— нет')}</b>"
    if clashes:
        text += "\n⚠️ Совпадения (задача может уйти не тому): " + "; ".join(esc(c_) for c_ in clashes)
    await message.answer(text)


@router.message(Command("report"))
async def cmd_report(message: Message, command: CommandObject, c: AppContainer) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "Напишите отчёт после команды, например:\n"
            "<code>/report авторизацию сделал, макет ещё в работе</code>"
        )
        return
    user = message.from_user
    key = user.username or user.full_name
    notes = await c.reconciliation.record_report(message.chat.id, key, text)
    if notes:
        await message.answer("Принял отчёт, обновил доску:\n" + "\n".join(esc(n) for n in notes))
    else:
        await message.answer("Принял отчёт ✅ (подходящих открытых задач не нашёл для авто-статуса)")


@router.message(Command("reconcile"))
async def cmd_reconcile(message: Message, c: AppContainer) -> None:
    digest, _ = c.reconciliation.evening_digest(message.chat.id)
    await message.answer(digest)


@router.message(Command("sync", "sync_board", "синхронизация"))
async def cmd_sync(message: Message, c: AppContainer) -> None:
    """Сверить память с доской: убрать «призраков» (удалённые на доске карточки)."""
    removed = await c.service.reconcile_with_board()
    c.persist()
    if removed:
        await message.answer(
            f"🧹 Синхронизировал с доской: убрал из памяти {removed} призрачных задач "
            f"(их карточек на доске уже нет). Теперь счётчики честные."
        )
    else:
        await message.answer("✅ Память и доска уже совпадают — призраков нет.")


@router.message(Command("remind"))
async def cmd_remind(message: Message, c: AppContainer) -> None:
    from ...scheduler.jobs import run_reminders

    if c.bot is None:
        c.bot = message.bot
    sent = await run_reminders(c)
    if sent == 0:
        await message.answer("Нет задач для напоминания сейчас 👍")


@router.message(Command("forget", "reset_team"))
async def cmd_forget(message: Message, c: AppContainer) -> None:
    count = len(c.team.all())
    if count == 0:
        await message.answer("Память о команде уже пуста — забывать некого 🙂")
        return
    await message.answer(
        f"⚠️ Забыть <b>всех участников</b> ({count})? Сотру имена, прозвища, "
        f"email и привязки к доске.\n"
        f"Задачи останутся — они хранятся на доске YouGile.",
        reply_markup=kb.forget_keyboard(),
    )


@router.message(Command("whoami"))
async def cmd_whoami(message: Message, c: AppContainer) -> None:
    user = message.from_user
    m = c.team.resolve(user.username or user.full_name)
    if m:
        await message.answer(
            f"Вы: <b>{esc(m.full_name or '—')}</b> (@{esc(m.username or '—')})\n"
            f"Алиасы: {esc(', '.join(m.aliases) or '—')}"
        )
    else:
        await message.answer("Я вас ещё не записал. Используйте /register.")
