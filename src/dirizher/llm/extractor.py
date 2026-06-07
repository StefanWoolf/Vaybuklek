"""Фабрика LLM-провайдера и фасад извлечения задач."""

from __future__ import annotations

from ..config import Settings
from ..logging_setup import get_logger
from .base import ExtractionContext, LLMProvider
from .mock_provider import MockLLMProvider

log = get_logger("dirizher.llm")


def build_provider(settings: Settings) -> LLMProvider:
    """Выбрать провайдера по настройкам с откатом в mock при отсутствии ключей."""
    provider = settings.llm.effective_provider
    if provider == "groq":
        from .groq_provider import GroqLLMProvider

        log.info("LLM-провайдер: Groq (%s)", settings.llm.groq_model)
        return GroqLLMProvider(settings.llm.groq_api_key, settings.llm.groq_model)
    if provider == "gigachat":
        from .gigachat_provider import GigaChatLLMProvider

        log.info("LLM-провайдер: GigaChat")
        return GigaChatLLMProvider(
            settings.llm.gigachat_credentials, settings.llm.gigachat_scope
        )
    log.info("LLM-провайдер: mock (эвристика, без сети)")
    return MockLLMProvider()


__all__ = ["build_provider", "ExtractionContext", "LLMProvider"]
