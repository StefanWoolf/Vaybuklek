"""Дешёвый предфильтр перед вызовом LLM — экономит токены, НЕ теряя задачи.

Принцип: пропускаем в LLM всё, что хоть как-то похоже на поручение, и молчим
ТОЛЬКО на заведомом мусоре (приветствия, реакции, эмодзи, короткие вопросы без
сигналов). При любом сомнении — возвращаем True (зовём LLM).
"""

from __future__ import annotations

import re
from datetime import date

from . import mock_provider as mp

_WORD = re.compile(r"[а-яёa-z0-9@]+", re.IGNORECASE)

# Обращения к боту — всегда обрабатываем
_BOT = ("дирижер", "дирижёр", "дережер", "@degree_case_bot")

# Чистые реакции/приветствия (если сообщение только из них — пропускаем мимо LLM)
_GREET = {
    "привет", "здаров", "хай", "ку", "спасибо", "спс", "пасиб", "ок", "окей", "окей",
    "да", "нет", "ага", "угу", "ха", "хаха", "хахах", "лол", "лул", "ладно", "норм",
    "хорошо", "хорош", "супер", "класс", "топ", "огонь", "плюс", "gg", "ok", "yes", "no",
    "пока", "споки", "доброе", "утро", "вечер", "всем",
}


def _has_deadline_cue(text: str) -> bool:
    today = date.today()
    return mp._parse_deadline(text, today) is not None or mp._parse_time(text) is not None


def looks_taskish(text: str, team_names: tuple[str, ...] | list[str] = ()) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    words = _WORD.findall(t)
    if not words:  # только эмодзи/пунктуация
        return False

    # ── Положительные сигналы → точно зовём LLM ──────────────────────────────
    if any(b in t for b in _BOT):
        return True
    if mp._is_task_command(text):              # «поставь таску», «запиши задачу»
        return True
    if mp._looks_like_task(text):              # глагол-поручение (сделай, подготовь…)
        return True
    if _has_deadline_cue(text):                # срок/время в тексте
        return True
    if "@" in t and len(words) >= 2:           # @упоминание + что-то
        return True
    for name in team_names:                    # «Имя, …» — поручение участнику
        nm = name.lstrip("@").lower()
        if nm and t.startswith(nm) and len(words) >= 2:
            return True

    # ── Негативные сигналы → пропускаем мимо LLM ─────────────────────────────
    if t.endswith("?"):                        # вопрос/обсуждение без сигналов
        return False
    if len(words) <= 3:                        # короткая реплика/реакция
        return False
    if all(w in _GREET for w in words):        # сплошь приветствия/реакции
        return False

    # Длинная содержательная фраза без явных сигналов — на всякий случай зовём LLM.
    return True
