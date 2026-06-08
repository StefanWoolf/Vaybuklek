"""Геймификация (RPG): опыт, уровни, ачивки, лидерборд.

Зачем (кейс, п.10): мотивировать команду закрывать задачи. За каждое
выполнение исполнитель получает XP, растёт в уровне-ранге, открывает ачивки;
вечером/по пятницам бот постит лидерборд.

Дизайн:
- Чистое ядро (функции `xp_for_completion`, `rank_for`, `_streak_after`) —
  легко тестируется без Telegram и без диска.
- `GamificationService` хранит профили в отдельном JSON (`./.data/gamification.json`,
  в .gitignore — там имена участников). Начисление идемпотентно по `task.id`,
  поэтому повторное закрытие/синхронизация доски не накручивает очки.
- Источник вызова — все точки «задача → Готово» (кнопка, отчёт, команда в чате).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape as _esc
from pathlib import Path

from pydantic import BaseModel, Field

from ..domain.enums import Priority
from ..domain.models import Task
from ..logging_setup import get_logger
from ..repository import TeamRegistry

log = get_logger("dirizher.game")

# ── Баланс начисления ────────────────────────────────────────────────────────
BASE_XP = 10
PRIORITY_BONUS = {Priority.low: 0, Priority.medium: 5, Priority.high: 15}
ON_TIME_BONUS = 10  # закрыл в срок (или у задачи нет дедлайна)

# Ранги: (порог накопленного XP, название, эмодзи). Уровень = индекс+1.
RANKS: list[tuple[int, str, str]] = [
    (0, "Новичок", "🐣"),
    (50, "Боец", "⚔️"),
    (150, "Мастер", "🛠️"),
    (300, "Эксперт", "🎓"),
    (600, "Гуру", "🧙"),
    (1000, "Легенда", "👑"),
]


class PlayerProfile(BaseModel):
    """Игровой профиль участника. Ключ — стабильный (username/имя в нижнем регистре)."""

    key: str
    display_name: str = ""
    xp: int = 0
    tasks_done: int = 0
    on_time: int = 0
    high_done: int = 0
    streak_days: int = 0
    last_done_date: date | None = None
    achievements: list[str] = Field(default_factory=list)  # коды разблокированных
    done_task_ids: list[str] = Field(default_factory=list)  # для идемпотентности


# ── Ачивки: (код, заголовок, условие) ────────────────────────────────────────
ACHIEVEMENTS: list[tuple[str, str, "object"]] = [
    ("first_blood", "🩸 Первая кровь", lambda p: p.tasks_done >= 1),
    ("five", "🖐️ Пятёрка", lambda p: p.tasks_done >= 5),
    ("ten", "🔟 Червонец", lambda p: p.tasks_done >= 10),
    ("fifty", "💎 Полста", lambda p: p.tasks_done >= 50),
    ("sniper", "🎯 Снайпер", lambda p: p.on_time >= 5),
    ("firefighter", "🚒 Пожарный", lambda p: p.high_done >= 1),
    ("streak3", "🔥 В ударе", lambda p: p.streak_days >= 3),
    ("streak7", "⚡ Неделя огня", lambda p: p.streak_days >= 7),
]
_ACH_TITLE = {code: title for code, title, _ in ACHIEVEMENTS}


# ── Чистое ядро ───────────────────────────────────────────────────────────────
def is_on_time(task: Task, today: date) -> bool:
    """В срок: дедлайна нет или закрыли не позже него."""
    return task.deadline is None or today <= task.deadline


def xp_for_completion(task: Task, *, on_time: bool) -> int:
    bonus = PRIORITY_BONUS.get(task.priority, 0)
    return BASE_XP + bonus + (ON_TIME_BONUS if on_time else 0)


def rank_for(xp: int) -> tuple[int, str, str, int | None]:
    """→ (уровень с 1, название, эмодзи, порог следующего уровня | None)."""
    level = 1
    name, emoji = RANKS[0][1], RANKS[0][2]
    for i, (threshold, nm, em) in enumerate(RANKS):
        if xp >= threshold:
            level, name, emoji = i + 1, nm, em
    next_threshold = RANKS[level][0] if level < len(RANKS) else None
    return level, name, emoji, next_threshold


def _streak_after(last: date | None, today: date, streak: int) -> int:
    """Новая серия дней с закрытиями после события «сегодня»."""
    if last == today:
        return max(streak, 1)  # уже считали сегодня
    if last is not None and (today - last) == timedelta(days=1):
        return streak + 1
    return 1


def progress_bar(xp: int, *, width: int = 10) -> str:
    """Текстовый прогресс до следующего уровня."""
    level, _name, _emoji, nxt = rank_for(xp)
    floor = RANKS[level - 1][0]
    if nxt is None:
        return "█" * width + " MAX"
    span = max(1, nxt - floor)
    filled = max(0, min(width, round((xp - floor) / span * width)))
    return "█" * filled + "░" * (width - filled) + f" {xp - floor}/{span}"


@dataclass
class Celebration:
    """Результат начисления для одного исполнителя (для сообщения в чат)."""

    display: str
    xp_gained: int
    total_xp: int
    level: int
    rank_name: str
    rank_emoji: str
    leveled_up: bool
    new_achievements: list[str] = field(default_factory=list)  # заголовки

    @property
    def line(self) -> str:
        """Короткая (без спама) строка-поздравление в HTML."""
        parts = [f"🎮 <b>{_esc(self.display)}</b> +{self.xp_gained} XP"]
        if self.leveled_up:
            parts.append(f"· уровень {self.level} {self.rank_emoji} «{self.rank_name}» 🆙")
        if self.new_achievements:
            parts.append("· 🏆 " + ", ".join(self.new_achievements))
        return " ".join(parts)


# ── Хранилище профилей ────────────────────────────────────────────────────────
class GameStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, PlayerProfile]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось прочитать игровое состояние (%s) — старт с нуля", e)
            return {}
        players: dict[str, PlayerProfile] = {}
        for key, raw in data.get("players", {}).items():
            try:
                players[key] = PlayerProfile(**raw)
            except Exception as e:  # noqa: BLE001
                log.warning("Пропущен игровой профиль %s: %s", key, e)
        return players

    def save(self, players: dict[str, PlayerProfile]) -> None:
        data = {"players": {k: p.model_dump(mode="json") for k, p in players.items()}}
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self._path)
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось сохранить игровое состояние: %s", e)
            if os.path.exists(tmp):
                os.unlink(tmp)


# ── Сервис ────────────────────────────────────────────────────────────────────
class GamificationService:
    """Начисление XP/ачивок и формирование лидерборда."""

    def __init__(self, store: GameStore, team: TeamRegistry) -> None:
        self.store = store
        self.team = team
        self._players: dict[str, PlayerProfile] = store.load()

    # — идентификация исполнителя в стабильный ключ —
    def _identify(self, raw_name: str) -> tuple[str, str]:
        member = self.team.resolve(raw_name)
        if member:
            key = (member.username or member.full_name or raw_name).strip().lower()
            display = member.full_name or member.username or raw_name
        else:
            key = raw_name.lstrip("@").strip().lower()
            display = raw_name.lstrip("@").strip()
        return key, display

    def _profile(self, key: str, display: str) -> PlayerProfile:
        p = self._players.get(key)
        if p is None:
            p = PlayerProfile(key=key, display_name=display)
            self._players[key] = p
        elif display and p.display_name != display:
            p.display_name = display  # имя могло уточниться
        return p

    def complete(self, task: Task, *, today: date | None = None) -> list[str]:
        """Начислить XP за закрытие задачи всем её исполнителям. Идемпотентно.

        Возвращает список готовых HTML-строк-поздравлений (пусто — если уже
        начисляли за эту задачу или исполнитель не указан)."""
        from .task_service import _split_assignees  # переиспользуем разбор «a, b»

        today = today or date.today()
        names = _split_assignees(task.assignee)
        lines: list[str] = []
        for name in names:
            cele = self._award(name, task, today)
            if cele:
                lines.append(cele.line)
        if lines:
            self.store.save(self._players)
        return lines

    def _award(self, raw_name: str, task: Task, today: date) -> Celebration | None:
        key, display = self._identify(raw_name)
        if not key:
            return None
        p = self._profile(key, display)
        if task.id in p.done_task_ids:
            return None  # уже начисляли — без двойного счёта
        p.done_task_ids.append(task.id)

        on_time = is_on_time(task, today)
        gain = xp_for_completion(task, on_time=on_time)
        old_level, *_ = rank_for(p.xp)

        p.xp += gain
        p.tasks_done += 1
        if on_time:
            p.on_time += 1
        if task.priority == Priority.high:
            p.high_done += 1
        p.streak_days = _streak_after(p.last_done_date, today, p.streak_days)
        p.last_done_date = today

        level, name, emoji, _nxt = rank_for(p.xp)
        unlocked: list[str] = []
        for code, title, cond in ACHIEVEMENTS:
            if code not in p.achievements and cond(p):
                p.achievements.append(code)
                unlocked.append(title)

        return Celebration(
            display=p.display_name or display,
            xp_gained=gain,
            total_xp=p.xp,
            level=level,
            rank_name=name,
            rank_emoji=emoji,
            leveled_up=level > old_level,
            new_achievements=unlocked,
        )

    def reset(self) -> int:
        """Обнулить весь лидерборд (например, убрать тестовые профили). → сколько удалено."""
        n = len(self._players)
        self._players = {}
        self.store.save(self._players)
        return n

    # — выборки/представление —
    def leaderboard(self, limit: int = 10) -> list[PlayerProfile]:
        ranked = [p for p in self._players.values() if p.xp > 0]
        ranked.sort(key=lambda p: (p.xp, p.tasks_done), reverse=True)
        return ranked[:limit]

    def profile_for(self, raw_name: str) -> PlayerProfile | None:
        key, _ = self._identify(raw_name)
        return self._players.get(key)

    def render_profile(self, raw_name: str) -> str:
        p = self.profile_for(raw_name)
        key, display = self._identify(raw_name)
        if p is None or p.xp == 0:
            return (
                f"🎮 <b>{_esc(display or 'Профиль')}</b>\n"
                "Пока нет очков. Закройте задачу — и начнём прокачку! ⚔️"
            )
        level, name, emoji, nxt = rank_for(p.xp)
        achs = ", ".join(_ACH_TITLE.get(c, c) for c in p.achievements) or "— пока нет"
        tail = f"\nДо следующего уровня: {nxt - p.xp} XP" if nxt else "\nМаксимальный ранг! 👑"
        return (
            f"🎮 <b>{_esc(p.display_name or display)}</b> — {emoji} {name} (ур. {level})\n"
            f"⭐ XP: <b>{p.xp}</b>  {progress_bar(p.xp)}\n"
            f"✅ Закрыто: {p.tasks_done}  ·  🎯 в срок: {p.on_time}  ·  🔥 серия: {p.streak_days} дн.\n"
            f"🏆 Ачивки: {achs}"
            f"{tail}"
        )

    def render_leaderboard(self, limit: int = 10) -> str:
        top = self.leaderboard(limit)
        if not top:
            return "🏆 <b>Лидерборд пуст</b>\nЗакройте первую задачу — и возглавьте таблицу!"
        medals = ["🥇", "🥈", "🥉"]
        lines = ["🏆 <b>Лидерборд</b>", ""]
        for i, p in enumerate(top):
            place = medals[i] if i < 3 else f"{i + 1}."
            _lvl, name, emoji, _ = rank_for(p.xp)
            lines.append(
                f"{place} <b>{_esc(p.display_name or p.key)}</b> — {p.xp} XP "
                f"{emoji} {name} · ✅{p.tasks_done}"
            )
        return "\n".join(lines)
