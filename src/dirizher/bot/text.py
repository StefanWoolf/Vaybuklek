"""Текстовые представления для Telegram (Markdown)."""

from __future__ import annotations

from datetime import date

from ..domain.models import Task
from ..services.task_service import Outcome, ProcessedTask

_WD_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _deadline_str(d: date | None) -> str:
    if not d:
        return "без срока"
    return f"{d.isoformat()} ({_WD_RU[d.weekday()]})"


def render_task_card(task: Task, *, header: str = "🆕 Новая задача") -> str:
    lines = [
        f"*{header}*",
        f"{task.priority.emoji} Приоритет: {task.priority.label_ru}",
        f"📋 {task.title}",
    ]
    if task.requirements:
        lines.append(f"📝 {task.requirements}")
    lines.append(f"👤 Исполнитель: {task.assignee or '—'}")
    lines.append(f"📅 Дедлайн: {_deadline_str(task.deadline)}")
    if task.sources:
        lines.append(f"📎 Источник: {task.sources[0].source.label_ru}")
    lines.append(f"🎯 Уверенность: {task.confidence:.2f}")
    return "\n".join(lines)


def render_processed(p: ProcessedTask) -> str:
    if p.outcome is Outcome.duplicate and p.duplicate_of:
        return (
            f"♻️ *Похоже на существующую задачу* (совпадение {p.dup_score:.2f}):\n"
            f"«{p.duplicate_of.title}»\n\n"
            f"Новая формулировка: «{p.task.title}»\n"
            f"Это та же задача?"
        )
    if p.outcome is Outcome.low_confidence:
        return (
            f"🤔 *Не уверен, что это задача* (уверенность {p.task.confidence:.2f}):\n"
            f"«{p.task.title}»\n\nЗавести её на доску?"
        )
    return render_task_card(p.task)


def render_board(cards) -> str:  # cards: list[BoardCard]
    if not cards:
        return "🗂️ Доска пуста."
    from ..domain.enums import TaskStatus

    buckets = {s: [] for s in TaskStatus}
    for c in cards:
        buckets[c.status].append(c)
    out = ["🗂️ *Канбан-доска*", ""]
    for status in TaskStatus:
        items = buckets[status]
        out.append(f"*{status.label_ru}* ({len(items)})")
        for c in items:
            who = f" — {c.assignee}" if c.assignee else ""
            out.append(f"  • {c.title}{who}")
        out.append("")
    return "\n".join(out).strip()


def render_created(task: Task) -> str:
    return (
        f"✅ Карточка создана на доске.\n"
        f"📋 {task.title}\n"
        f"👤 {task.assignee or '—'} · 📅 {_deadline_str(task.deadline)}\n"
        f"🆔 `{task.board_card_id}`"
    )
