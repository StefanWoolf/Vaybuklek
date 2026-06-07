"""Вечерняя сверка отчётов с доской.

Практика команды: вечером каждый отписывается о статусе. Дирижёр:
1) принимает отчёт (/report или обычным сообщением-отчётом),
2) сопоставляет его с открытыми задачами исполнителя и проставляет статусы,
3) вечером формирует сводку и тегает тех, кто не отписался.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from html import escape as _esc

from ..domain.enums import TaskStatus
from ..logging_setup import get_logger
from ..repository import TaskRepository, TeamRegistry
from .task_service import TaskService

log = get_logger("dirizher.reconcile")

_DONE_WORDS = ["готов", "сделал", "закрыл", "done", "выполн", "заверш", "доделал", "залил"]
_PROGRESS_WORDS = ["в работе", "делаю", "начал", "пилю", "in progress", "продолжаю", "ещё не"]
_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 3}


@dataclass
class DailyReports:
    """Кто отписался в конкретном чате за конкретный день."""

    day: date
    reported: set[str] = field(default_factory=set)  # ключи участников (username/имя)


class ReconciliationService:
    def __init__(self, repo: TaskRepository, team: TeamRegistry, service: TaskService) -> None:
        self.repo = repo
        self.team = team
        self.service = service
        self._reports: dict[int, DailyReports] = {}

    def _today_reports(self, chat_id: int, today: date) -> DailyReports:
        dr = self._reports.get(chat_id)
        if dr is None or dr.day != today:
            dr = DailyReports(day=today)
            self._reports[chat_id] = dr
        return dr

    async def record_report(
        self, chat_id: int, member_key: str, text: str, *, today: date | None = None
    ) -> list[str]:
        """Учесть отчёт участника, проставить статусы задач. Вернуть заметки об изменениях."""
        today = today or date.today()
        self._today_reports(chat_id, today).reported.add(member_key.lstrip("@").lower())

        low = text.lower()
        is_done = any(w in low for w in _DONE_WORDS)
        is_progress = any(w in low for w in _PROGRESS_WORDS)
        rep_tokens = _tokens(text)

        open_tasks = self.repo.open_by_assignee(member_key)
        notes: list[str] = []
        for t in open_tasks:
            overlap = _tokens(t.title) & rep_tokens
            # привязываем по совпадению ключевых слов; если открытая задача одна —
            # обобщённый отчёт «всё готово» применяем к ней
            relevant = bool(overlap) or len(open_tasks) == 1
            if not relevant:
                continue
            if is_done:
                await self.service.set_status(t, TaskStatus.done)
                notes.append(f"✅ «{t.title}» → Готово")
            elif is_progress:
                await self.service.set_status(t, TaskStatus.in_progress)
                notes.append(f"▶️ «{t.title}» → В работе")
        return notes

    def evening_digest(self, chat_id: int, *, today: date | None = None) -> tuple[str, list[str]]:
        """Сводка дня + список упоминаний тех, кто не отписался."""
        today = today or date.today()
        reported = self._today_reports(chat_id, today).reported

        open_tasks = self.repo.open()
        by_assignee: dict[str, list] = {}
        for t in open_tasks:
            owners = t.assignees or ["—"]
            for owner in owners:
                by_assignee.setdefault(owner, []).append(t)

        lines = [f"🌙 <b>Вечерняя сверка</b> · {today.isoformat()}", ""]
        if not open_tasks:
            lines.append("Открытых задач нет — отличная работа! 🎉")
            return "\n".join(lines), []

        silent: list[str] = []
        for assignee, tasks in sorted(by_assignee.items()):
            key = assignee.lstrip("@").lower()
            mark = "✅ отчитался" if key in reported else "🔕 нет отчёта"
            lines.append(f"<b>{_esc(assignee)}</b> — {len(tasks)} откр. задач — {mark}")
            for t in tasks:
                lines.append(f"  • [{t.status.label_ru}] {_esc(t.title)} (до {_esc(t.deadline_display())})")
            if key not in reported and assignee != "—":
                silent.append(self.team.mention_for(assignee))
            lines.append("")

        if silent:
            lines.append("Не отписались: " + ", ".join(silent) + " — отметьтесь, пожалуйста 🙏")
        return "\n".join(lines).strip(), silent
