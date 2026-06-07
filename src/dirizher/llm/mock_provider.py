"""Mock-провайдер: извлекает задачи эвристикой, без сети.

Намеренно детерминированный — обеспечивает работоспособное демо и стабильные
тесты, когда ключей LLM ещё нет. Покрывает типовой сценарий из отчёта:
«Максим, сделай авторизацию к четвергу».
"""

from __future__ import annotations

import re
from datetime import date, time, timedelta

from ..domain.enums import Priority
from ..domain.models import ExtractedTask
from .base import ExtractionContext

# Глаголы-триггеры поручения (начальная форма основы → совпадаем по префиксу)
_TRIGGERS = [
    "сделай", "сделать", "подготов", "исправ", "поправ", "добав", "реализ",
    "проверь", "настро", "напиш", "оформ", "собер", "создай", "создать",
    "запус", "выгруз", "залей", "залить", "обнови", "обновить", "свяжись",
    "отправь", "отправить", "разработ", "посмотри", "посчитай", "согласуй",
    "выстав", "почини", "задеплой", "опиши", "протестируй", "затест",
    "нужно", "надо", "давай", "запланируй", "назначь",
]

# Стемы дней недели (ловят любые падежи: «понедельник», «к понедельнику», ...)
_WEEKDAYS = {
    0: r"понедельник\w*|\bпн\b",
    1: r"вторник\w*|\bвт\b",
    2: r"сред[ауы]\w*|\bср\b",
    3: r"четверг\w*|\bчт\b",
    4: r"пятниц\w+|\bпт\b",
    5: r"суббот\w+|\bсб\b",
    6: r"воскресень\w*|\bвс\b",
}

_HIGH = ["срочно", "asap", "горит", "критич", "сегодня же", "немедленно"]
_LOW = ["не срочно", "когда будет время", "не горит", "по возможности"]

# Директивы смены приоритета в правках. Ловят императивы и любые формы по основе:
# «повысь / повысить / повысьте приоритет», «понизь», «сделай срочной» и т.п.
_PRIORITY_UP = (
    "повыс", "подним", "подыми", "поднять", "приоритетн", "поважнее", "важнее",
    "срочн", "asap", "горит", "критич", "немедленно", "сегодня же", "повыше",
)
_PRIORITY_DOWN = (
    "не срочн", "не важн", "неважн", "понизь", "пониз", "пониж", "снизь", "снизить",
    "пониже", "по возможности", "когда будет время", "не горит",
)


def detect_priority_change(text: str) -> Priority | None:
    """Распознать директиву смены приоритета в тексте правки (или None).

    Понижение проверяем первым, чтобы «не срочно» не попало в повышение.
    """
    low = text.lower()
    if any(k in low for k in _PRIORITY_DOWN):
        return Priority.low
    if any(k in low for k in _PRIORITY_UP):
        return Priority.high
    return None

_NAME_RE = re.compile(r"^\s*(@\w+|[А-ЯЁ][а-яё]+)\s*[,:]\s*")


def _next_weekday(today: date, target: int) -> date:
    delta = (target - today.weekday()) % 7
    delta = delta or 7  # «к пятнице», когда сегодня пятница => следующая
    return today + timedelta(days=delta)


def _parse_deadline(text: str, today: date) -> date | None:
    low = text.lower()
    if "послезавтра" in low:
        return today + timedelta(days=2)
    if "завтра" in low:
        return today + timedelta(days=1)
    if "сегодня" in low:
        return today
    if "конца недели" in low or "конец недели" in low:
        return _next_weekday(today, 4)  # пятница
    # явная дата DD.MM(.YYYY)
    m = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b", low)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = today.year if not y else (2000 + int(y) if int(y) < 100 else int(y))
        try:
            return date(year, mth, d)
        except ValueError:
            pass
    for wd, pattern in _WEEKDAYS.items():
        if re.search(pattern, low):
            return _next_weekday(today, wd)
    return None


def _parse_time(text: str) -> time | None:
    """Вытащить время суток: «в 19:00», «к 9 утра», «в 18», «в 18.30»."""
    low = text.lower()
    m = re.search(r"\b(\d{1,2})[:.](\d{2})\b", low)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return time(h, mi)
    m = re.search(r"\b(?:в|к|на)\s+(\d{1,2})\s*(утра|вечера|дня|ночи|час\w*)?\b", low)
    if m:
        h = int(m.group(1))
        part = m.group(2) or ""
        if part.startswith(("вечера", "ночи")) and h < 12:
            h += 12
        if 0 <= h <= 23:
            return time(h, 0)
    return None


def _detect_priority(text: str) -> Priority:
    low = text.lower()
    if any(k in low for k in _LOW):
        return Priority.low
    if any(k in low for k in _HIGH):
        return Priority.high
    return Priority.medium


def _match_assignee(text: str, ctx: ExtractionContext) -> tuple[str | None, str]:
    """Вернуть (исполнитель, текст_без_префикса_имени)."""
    m = _NAME_RE.match(text)
    if m:
        return m.group(1), text[m.end():]
    # имя где-то в тексте по словарю команды
    low = text.lower()
    for name in ctx.team_names():
        bare = name.lstrip("@").lower()
        if bare and re.search(rf"\b{re.escape(bare)}\b", low):
            return name, text
    return None, text


def _looks_like_task(clause: str) -> bool:
    low = clause.lower()
    return any(t in low for t in _TRIGGERS)


# Явное переименование: «переименуй в …», «назови …», «название: …».
_RENAME_RE = re.compile(
    r"\b(?:переименуй\w*|переназови|назови|смени\s+назван\w*|измени\s+назван\w*|"
    r"новое\s+назван\w*|назван\w*|заголовок)\b\s*(?:задач\w*\s*)?(?:на|в|:)?\s*"
    r"[«\"']?(.+?)[»\"']?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def detect_rename(text: str) -> str | None:
    """Распознать явную директиву переименования и вернуть новый заголовок."""
    m = _RENAME_RE.search(text.strip())
    if not m:
        return None
    title = m.group(1).strip(" \t\n.,—-«»\"'")
    return title or None


# Директивы по полям задачи (исполнитель/срок) — для отсева от «сути» правки.
_ASSIGN_DIRECTIVE = re.compile(
    r"\b(назнач\w*|повес\w*|переназнач\w*|поручи\w*|исполнител\w*)\b\s*(?:на\s+)?",
    re.IGNORECASE,
)
_DEADLINE_DIRECTIVE = re.compile(
    r"\b(перенес\w*|сдвин\w*|дедлайн\w*|срок\w*)\b",
    re.IGNORECASE,
)


def _strip_field_directives(text: str, assignee_raw: str | None) -> str:
    """Убрать директивы по полям (приоритет/срок/исполнитель), оставив «суть».
    Если после очистки осталась осмысленная формулировка — правка переписывает задачу."""
    t = text
    for kw in (*_PRIORITY_UP, *_PRIORITY_DOWN):
        t = re.sub(re.escape(kw) + r"\w*", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bприоритет\w*\b", " ", t, flags=re.IGNORECASE)
    t = _ASSIGN_DIRECTIVE.sub(" ", t)
    if assignee_raw:
        t = re.sub(re.escape(assignee_raw.lstrip("@")) + r"\w*", " ", t, flags=re.IGNORECASE)
    t = _DEADLINE_DIRECTIVE.sub(" ", t)
    t = re.sub(r"\bна\b", " ", t)  # остаточный предлог после «назначь на»/«перенеси на»
    return re.sub(r"\s{2,}", " ", t).strip(" \t\n.,—-")


def correction_is_reformulation(correction: str, assignee_raw: str | None) -> bool:
    """True, если правка переформулирует саму задачу (а не только её поля).

    После отсева директив должно остаться содержимое сверх самого глагола-триггера —
    тогда это новая формулировка («сделай авторизацию»), а не «сделай срочной».
    """
    residual = _strip_field_directives(correction, assignee_raw)
    if not _looks_like_task(residual):
        return False
    words = [w for w in re.split(r"\W+", residual) if w]
    non_trigger = [w for w in words if not any(w.lower().startswith(t) for t in _TRIGGERS)]
    return len(non_trigger) >= 1


_TASK_COMMANDS = (
    "поставь таск", "поставь задач", "сделай задач", "сделай таск", "заведи задач",
    "запиши задач", "запиши это", "зафиксируй", "оформи задач", "добавь задач",
    "создай задач",
)


def _is_task_command(message: str) -> bool:
    """Короткое сообщение-команда «зафиксируй задачу» без своих деталей."""
    low = message.strip().lower()
    return len(low) <= 40 and any(cmd in low for cmd in _TASK_COMMANDS)


def _clean_title(text: str, deadline_present: bool) -> str:
    t = text.strip(" \t\n.—-")
    # 1) убрать модификаторы приоритета/вежливости (они могут стоять после срока)
    t = re.sub(r"\b(срочно|asap|пожалуйста|плиз|немедленно)\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s{2,}", " ", t).strip(" \t\n.,—-")
    # 2) срезать срок (в начале или в конце) для чистоты заголовка
    deadline_phrase = (
        r"(к|до|в|на)\s+(завтра|послезавтра|сегодня|конца недели|"
        r"понедельник\w*|вторник\w*|сред\w+|четверг\w*|пятниц\w+|суббот\w+|"
        r"воскресень\w*|\d{1,2}[.\-/]\d{1,2}(?:[.\-/]\d{2,4})?)"
    )
    t = re.sub(rf"\s*{deadline_phrase}\b\.?\s*$", "", t, flags=re.IGNORECASE)  # хвост
    t = re.sub(rf"^{deadline_phrase}\b[\s,]*", "", t, flags=re.IGNORECASE)      # начало
    # время суток («в 20:00», «к 9 утра», «в 18»)
    t = re.sub(r"\s*(в|к|на)?\s*\b\d{1,2}[:.]\d{2}\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*(в|к|на)\s+\d{1,2}\s*(утра|вечера|дня|ночи|час\w*)\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s{2,}", " ", t).strip(" \t\n.,—-")
    return t[:1].upper() + t[1:] if t else text.strip()


class MockLLMProvider:
    name = "mock"

    async def extract_tasks(
        self, message: str, context: ExtractionContext
    ) -> list[ExtractedTask]:
        # Команда «зафиксируй задачу» без деталей → ищем задачу в недавней переписке.
        if _is_task_command(message) and context.recent_dialog:
            # убираем префиксы «Автор: », чтобы не путать их с исполнителем
            lines = [d.split(": ", 1)[1] if ": " in d else d for d in context.recent_dialog]
            found = await self._extract_from("\n".join(lines), context)
            if found:
                return found
        return await self._extract_from(message, context)

    async def _extract_from(
        self, message: str, context: ExtractionContext
    ) -> list[ExtractedTask]:
        clauses = re.split(r"[;\n]+|(?<=[.!?])\s+", message.strip())
        out: list[ExtractedTask] = []
        for clause in clauses:
            clause = clause.strip()
            if len(clause) < 4 or not _looks_like_task(clause):
                continue
            assignee, rest = _match_assignee(clause, context)
            deadline = _parse_deadline(clause, context.today)
            tm = _parse_time(clause)
            priority = _detect_priority(clause)
            title = _clean_title(rest, deadline is not None)
            if not title:
                continue

            confidence = 0.6
            if assignee:
                confidence += 0.2
            if deadline:
                confidence += 0.1
            confidence = min(confidence + 0.05, 0.95)

            out.append(
                ExtractedTask(
                    task=title,
                    assignee=assignee.lstrip("@") if assignee else None,
                    deadline=deadline,
                    deadline_time=tm,
                    priority=priority,
                    confidence=round(confidence, 2),
                )
            )
        return out
