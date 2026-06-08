"""Текстовые представления для Telegram (parse_mode=HTML).

Используем HTML, а не Markdown: в HTML спец-символы только < > &, их экранируем
в динамике (заголовки задач, имена, @user_name) — поэтому подчёркивания,
звёздочки и т.п. в тексте задач больше не ломают отправку.
"""

from __future__ import annotations

from html import escape as esc

from ..domain.models import Task
from ..services.task_service import Outcome, ProcessedTask


def render_task_card(task: Task, *, header: str = "🆕 Новая задача") -> str:
    lines = [
        f"<b>{esc(header)}</b>",
        f"{task.priority.emoji} Приоритет: {task.priority.label_ru}",
        f"📋 {esc(task.title)}",
    ]
    if task.requirements:
        lines.append(f"📝 {esc(task.requirements)}")
    lines.append(f"👤 Исполнитель: {esc(task.assignee or '—')}")
    lines.append(f"📅 Дедлайн: {esc(task.deadline_display())}")
    if task.sources:
        lines.append(f"📎 Источник: {esc(task.sources[0].source.label_ru)}")
    return "\n".join(lines)


def render_processed(p: ProcessedTask) -> str:
    if p.outcome is Outcome.duplicate and p.duplicate_of:
        return (
            f"♻️ <b>Похоже на существующую задачу</b> (совпадение {p.dup_score:.2f}):\n"
            f"«{esc(p.duplicate_of.title)}»\n\n"
            f"Новая формулировка: «{esc(p.task.title)}»\n"
            f"Это та же задача?"
        )
    if p.outcome is Outcome.low_confidence:
        return (
            f"🤔 <b>Возможно, это задача:</b>\n"
            f"«{esc(p.task.title)}»\n\nЗавести её на доску?"
        )
    return render_task_card(p.task)


def render_board(cards) -> str:  # cards: list[BoardCard]
    if not cards:
        return "🗂️ Доска пуста."
    from ..domain.enums import TaskStatus

    buckets = {s: [] for s in TaskStatus}
    for c in cards:
        buckets[c.status].append(c)
    out = ["🗂️ <b>Канбан-доска</b>", ""]
    for status in TaskStatus:
        items = buckets[status]
        out.append(f"<b>{status.label_ru}</b> ({len(items)})")
        for c in items:
            who = f" — {esc(c.assignee)}" if c.assignee else ""
            due = f" · 📅 {c.deadline.isoformat()}" if getattr(c, "deadline", None) else ""
            out.append(f"  • {esc(c.title)}{who}{due}")
        out.append("")
    return "\n".join(out).strip()


def render_board_task(card) -> str:  # card: BoardCard
    """Карточка задачи с доски для интерактивного списка «мои задачи»."""
    lines = [
        f"📋 <b>{esc(card.title)}</b>",
        f"🔸 Статус: {card.status.label_ru}",
    ]
    if card.assignee:
        lines.append(f"👤 {esc(card.assignee)}")
    if getattr(card, "deadline", None):
        lines.append(f"📅 {card.deadline.isoformat()}")
    return "\n".join(lines)


def render_created(task: Task) -> str:
    return (
        f"✅ Карточка создана на доске.\n"
        f"📋 {esc(task.title)}\n"
        f"👤 {esc(task.assignee or '—')} · 📅 {esc(task.deadline_display())}\n"
        f"🆔 <code>{esc(task.board_card_id or '')}</code>"
    )
