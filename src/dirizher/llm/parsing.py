"""Надёжный разбор JSON-ответа LLM в список ExtractedTask."""

from __future__ import annotations

import json
import re

from ..domain.models import ExtractedTask
from ..logging_setup import get_logger

log = get_logger("dirizher.llm.parsing")

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _strip_fences(raw: str) -> str:
    m = _FENCE.search(raw)
    return m.group(1) if m else raw


def _extract_json_object(raw: str) -> str:
    """Вырезать первый сбалансированный {...} — LLM иногда добавляет лишний текст."""
    start = raw.find("{")
    if start == -1:
        return raw
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return raw[start:]


def parse_tasks(raw: str) -> list[ExtractedTask]:
    """Преобразовать сырой ответ LLM в список валидных задач.

    Невалидные элементы пропускаются (а не роняют весь разбор).
    """
    text = _extract_json_object(_strip_fences(raw.strip()))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning("Не удалось распарсить JSON LLM: %s | raw=%.200s", e, raw)
        return []

    items = data.get("tasks", data) if isinstance(data, dict) else data
    if isinstance(items, dict):  # одиночная задача без обёртки
        items = [items]
    if not isinstance(items, list):
        return []

    result: list[ExtractedTask] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            result.append(ExtractedTask.model_validate(_normalize(item)))
        except Exception as e:  # noqa: BLE001
            log.warning("Пропущена невалидная задача %s: %s", item, e)
    return result


def _normalize(item: dict) -> dict:
    """Привести строковые null/пустышки к None; смапить ключ time → deadline_time."""
    out = dict(item)
    # LLM возвращает ключ "time"; внутреннее поле называется deadline_time
    if "time" in out and "deadline_time" not in out:
        out["deadline_time"] = out.pop("time")
    for key in ("assignee", "deadline", "deadline_time", "requirements"):
        v = out.get(key)
        if isinstance(v, str) and v.strip().lower() in {"", "null", "none", "—", "-"}:
            out[key] = None
    if "confidence" not in out:
        out["confidence"] = 0.5
    return out
