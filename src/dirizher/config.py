"""Конфигурация Дирижёра.

Вся конфигурация читается из переменных окружения / файла `.env`
(см. `.env.example`). В коде нет ни одного захардкоженного секрета.

Принцип «деградации в mock»: если ключ внешнего сервиса не задан,
соответствующий компонент переходит в mock-режим и система остаётся
полностью работоспособной офлайн (важно для демо и для CI).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramSettings(BaseSettings):
    bot_token: str = ""
    mode: str = "polling"  # polling | webhook
    webhook_base_url: str = ""
    team_chat_id: int | None = None

    @field_validator("team_chat_id", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        # Пустое значение в .env (TEAM_CHAT_ID=) трактуем как «не задано»
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @property
    def is_mock(self) -> bool:
        return not self.bot_token


class LLMSettings(BaseSettings):
    provider: str = "mock"  # mock | groq | gigachat
    # >= confidence_threshold → задача; [ignore_threshold; confidence) → уточнить;
    # < ignore_threshold → молча игнорируем (чтобы бот не спамил на болтовню).
    confidence_threshold: float = 0.7
    ignore_threshold: float = 0.6

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    gigachat_credentials: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"

    @property
    def effective_provider(self) -> str:
        """Провайдер с учётом наличия ключей — иначе откат в mock."""
        if self.provider == "groq" and self.groq_api_key:
            return "groq"
        if self.provider == "gigachat" and self.gigachat_credentials:
            return "gigachat"
        return "mock"


class YouGileSettings(BaseSettings):
    api_key: str = ""
    base_url: str = "https://ru.yougile.com/api-v2"
    column_todo: str = ""
    column_in_progress: str = ""
    column_done: str = ""

    @property
    def is_mock(self) -> bool:
        return not self.api_key


class MemorySettings(BaseSettings):
    # backend: lexical | chroma. По умолчанию lexical — без сетевых загрузок.
    # chroma включает семантический поиск (скачает embedding-модель при 1-м старте).
    backend: str = "lexical"
    chroma_path: str = "./.data/chroma"
    dedup_threshold: float = 0.83
    project_snapshot: str = "./.data/project.md"


class AudioSettings(BaseSettings):
    enabled: bool = False
    whisper_model: str = "small"
    groq_api_key: str = ""
    hf_token: str = ""

    @property
    def is_mock(self) -> bool:
        return not self.enabled


class ScheduleSettings(BaseSettings):
    reminder_cron: str = "0 10,15 * * *"
    evening_reconcile_cron: str = "0 20 * * *"
    remind_before_hours: int = 24


class APISettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8080
    shared_secret: str = ""


class Settings(BaseSettings):
    """Корневые настройки. Вложенные секции через двойное подчёркивание:
    например `DIRIZHER_TELEGRAM__BOT_TOKEN`.
    """

    model_config = SettingsConfigDict(
        env_prefix="DIRIZHER_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    timezone: str = "Europe/Moscow"
    log_level: str = "INFO"

    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    yougile: YouGileSettings = Field(default_factory=YouGileSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    api: APISettings = Field(default_factory=APISettings)

    def mode_banner(self) -> str:
        """Однострочный отчёт, какие компоненты боевые, а какие в mock."""
        flag = lambda mock: "mock" if mock else "live"  # noqa: E731
        return (
            f"telegram={flag(self.telegram.is_mock)} "
            f"llm={self.llm.effective_provider} "
            f"yougile={flag(self.yougile.is_mock)} "
            f"audio={flag(self.audio.is_mock)}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
