"""Живой MD-снимок проекта — человекочитаемый «снимок», который бот обновляет сам.

Содержит команду, открытые задачи и недавние решения. Используется и как
контекст для LLM, и как артефакт прозрачности для людей.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..domain.models import Task, TeamMember
from ..logging_setup import get_logger

log = get_logger("dirizher.memory.snapshot")


class ProjectSnapshot:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._decisions: list[str] = []

    def add_decision(self, text: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._decisions.append(f"- {stamp} — {text}")
        self._decisions = self._decisions[-20:]

    def render(self, team: list[TeamMember], tasks: list[Task]) -> str:
        lines = ["# Снимок проекта «Дирижёр»", ""]
        lines.append(f"_Обновлено: {datetime.now().strftime('%Y-%m-%d %H:%M')}_")
        lines += ["", "## Команда", ""]
        if team:
            for m in team:
                handle = f" (@{m.username})" if m.username else ""
                lines.append(f"- {m.full_name or m.mention()}{handle}")
        else:
            lines.append("- (пока не зарегистрирована)")

        lines += ["", "## Открытые задачи", ""]
        open_tasks = [t for t in tasks if t.status.value != "done"]
        if open_tasks:
            for t in open_tasks:
                dl = t.deadline.isoformat() if t.deadline else "без срока"
                who = t.assignee or "—"
                lines.append(f"- [{t.priority.emoji}] {t.title} — {who} — до {dl}")
        else:
            lines.append("- (нет открытых задач)")

        lines += ["", "## Недавние решения", ""]
        lines += self._decisions[-10:] or ["- (нет записей)"]
        return "\n".join(lines) + "\n"

    def save(self, team: list[TeamMember], tasks: list[Task]) -> str:
        content = self.render(team, tasks)
        self._path.write_text(content, encoding="utf-8")
        return content

    def context_for_llm(self, tasks: list[Task], limit: int = 8) -> str:
        """Короткая сводка открытых задач — как контекст памяти для LLM."""
        open_tasks = [t for t in tasks if t.status.value != "done"][:limit]
        if not open_tasks:
            return ""
        return "Открытые задачи: " + "; ".join(
            f"{t.title} ({t.assignee or '—'})" for t in open_tasks
        )
