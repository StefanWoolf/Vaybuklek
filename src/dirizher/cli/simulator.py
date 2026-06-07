"""Консольный симулятор: прогоняет полную цепочку «чат → задача → доска»
без Telegram. Полезно для демо и ручной проверки, когда токена ещё нет.

Запуск:  python -m dirizher.cli.simulator
"""

from __future__ import annotations

import asyncio

from ..container import AppContainer
from ..domain.enums import TaskSource, TaskStatus
from ..domain.models import SourceRef, TeamMember
from ..logging_setup import setup_logging
from ..services.task_service import Outcome
from ..bot import text as tx

DEMO_TEAM = [
    TeamMember(user_id=1, username="maxim", full_name="Максим Кано", aliases=["Максим", "Макс"]),
    TeamMember(user_id=2, username="dasha", full_name="Дарья Смоловая", aliases=["Даша", "Дарья"]),
    TeamMember(user_id=3, username="andrey", full_name="Андрей Скрипа", aliases=["Андрей"]),
]

BANNER = """\
╔══════════════════════════════════════════════════════════════╗
║   🎼  ДИРИЖЁР — консольный симулятор (демо без Telegram)        ║
╚══════════════════════════════════════════════════════════════╝
Пишите сообщения как в командном чате — я извлеку задачи.
Команды: /board  /mode [auto|manual]  /tasks <имя>  /team  /help  /quit
"""

HELP = """\
Примеры сообщений:
  Максим, сделай авторизацию к четвергу
  Даша, подготовь макет до пятницы, срочно
  нужно поправить баг с логином        (низкая уверенность → уточнение)
Команды:
  /board              показать канбан-доску
  /mode auto|manual   переключить режим отправки задач
  /tasks maxim        открытые задачи участника
  /team               список команды
  /quit               выход
"""


def _plain(s: str) -> str:
    """Убрать HTML-теги/сущности для вывода в консоль."""
    import html
    import re

    return html.unescape(re.sub(r"<[^>]+>", "", s))


def _print_card(task) -> None:
    print("\n" + _plain(tx.render_task_card(task)))


async def _handle_processed(c: AppContainer, processed, chat_id: int) -> None:
    auto = c.mode.is_auto(chat_id)
    for p in processed:
        if p.outcome is Outcome.new:
            _print_card(p.task)
            if auto or _ask("Создать карточку?", "yes"):
                created = await c.service.create_on_board(p.task)
                print(f"  ✅ Создано на доске: {created.board_card_id}")
            else:
                if _ask("Поправить?", "no"):
                    corr = input("  правка> ").strip()
                    await c.service.apply_correction(p.task, corr)
                    _print_card(p.task)
                    if _ask("Создать теперь?", "yes"):
                        created = await c.service.create_on_board(p.task)
                        print(f"  ✅ Создано на доске: {created.board_card_id}")
                else:
                    print("  ❌ Отклонено")
        elif p.outcome is Outcome.duplicate and p.duplicate_of:
            print(f"\n  ♻️ Похоже на «{p.duplicate_of.title}» (совпадение {p.dup_score})")
            if auto or _ask("Объединить?", "yes"):
                await c.service.merge_duplicate(p.duplicate_of, p.task.sources[0])
                print("  🔗 Источники объединены")
            else:
                created = await c.service.create_on_board(p.task)
                print(f"  ➕ Создана новая: {created.board_card_id}")
        else:  # low confidence
            print(f"\n  🤔 Не уверен, что это задача (уверенность {p.task.confidence}): «{p.task.title}»")
            if _ask("Завести?", "no"):
                created = await c.service.create_on_board(p.task)
                print(f"  ✅ Создано: {created.board_card_id}")
            else:
                print("  🚫 Пропускаю")


def _ask(question: str, default: str) -> bool:
    hint = "Y/n" if default == "yes" else "y/N"
    ans = input(f"  {question} [{hint}] ").strip().lower()
    if not ans:
        return default == "yes"
    return ans in {"y", "yes", "д", "да"}


async def main() -> None:
    setup_logging("WARNING")  # тихо, чтобы не мешать диалогу
    c = AppContainer()
    for m in DEMO_TEAM:
        c.team.register(m)
    chat_id = 1000
    print(BANNER)
    print(f"Режимы компонентов: {c.settings.mode_banner()}")
    print(f"Режим отправки: {'АВТО' if c.mode.is_auto(chat_id) else 'С ПОДТВЕРЖДЕНИЕМ'}\n")

    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in {"/quit", "/exit"}:
            break
        if line == "/help":
            print(HELP)
            continue
        if line == "/board":
            cards = await c.board.list_cards()
            print(_plain(tx.render_board(cards)))
            continue
        if line == "/team":
            for m in c.team.all():
                print(f"  • {m.full_name} (@{m.username}) алиасы: {', '.join(m.aliases)}")
            continue
        if line.startswith("/mode"):
            arg = line[5:].strip().lower()
            if arg in {"auto", "авто"}:
                c.mode.set_auto(chat_id, True)
            elif arg in {"manual", "ручной"}:
                c.mode.set_auto(chat_id, False)
            print(f"  Режим: {'АВТО' if c.mode.is_auto(chat_id) else 'С ПОДТВЕРЖДЕНИЕМ'}")
            continue
        if line.startswith("/tasks"):
            name = line[6:].strip()
            for t in c.repo.open_by_assignee(name):
                print(f"  • {t.title} — {t.assignee} — до {t.deadline or '—'}")
            continue

        source = SourceRef(source=TaskSource.chat, chat_id=chat_id, excerpt=line[:200])
        processed = await c.service.ingest(line, source)
        if not processed:
            print("  (задач не нашёл)")
            continue
        await _handle_processed(c, processed, chat_id)

    await c.aclose()
    print("\nПока! 🎼")


if __name__ == "__main__":
    asyncio.run(main())
