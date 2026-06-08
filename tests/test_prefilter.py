"""Тесты предфильтра LLM и ротации ключей Groq."""

import pytest

from dirizher.config import LLMSettings
from dirizher.llm.prefilter import looks_taskish

NAMES = ("Данила", "Андрей", "danya")


@pytest.mark.parametrize("text", [
    "привет", "ок", "спасибо!", "хаха))", "👍🔥", "Что думаешь сделаем?",
    "может обнову сделаем?", "да", "норм",
])
def test_prefilter_skips_noise(text):
    assert looks_taskish(text, NAMES) is False


@pytest.mark.parametrize("text", [
    "Данила сделай бота к среде",
    "@danya закрыл задачу по презентации",
    "поставь задачу",
    "Дирижер какие у меня задачи",
    "Данила и Андрей выполните фикс API до завтра",
    "надо подготовить отчёт к пятнице",
])
def test_prefilter_passes_tasks(text):
    assert looks_taskish(text, NAMES) is True


def test_groq_key_list_dedup_and_merge():
    s = LLMSettings(groq_api_key="k1", groq_api_keys="k2, k3 , k1")
    assert s.groq_key_list == ["k1", "k2", "k3"]


def test_groq_key_list_empty():
    assert LLMSettings().groq_key_list == []
