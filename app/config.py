"""Конфигурация приложения: читается из .env один раз при старте."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Бот ---------------------------------------------------------------
    bot_token: str = Field(alias="BOT_TOKEN")
    # Хранится строкой намеренно: pydantic-settings пытается разобрать значение
    # для list[int] как JSON ещё до валидаторов, и "111,222" его роняет.
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")

    # --- Telethon (аккаунт-парсер) -----------------------------------------
    tg_api_id: int = Field(alias="TG_API_ID")
    tg_api_hash: str = Field(alias="TG_API_HASH")
    tg_phone: str = Field(default="", alias="TG_PHONE")
    tg_session: str = Field(default="autochecker", alias="TG_SESSION")
    # Готовая строка сессии (Telethon StringSession). Если задана — используется
    # вместо файла .session, и python -m app.tools.login не нужен.
    tg_session_string: str = Field(default="", alias="TG_SESSION_STRING")

    # --- Upwork (опционально) -----------------------------------------------
    upwork_client_id: str = Field(default="", alias="UPWORK_CLIENT_ID")
    upwork_client_secret: str = Field(default="", alias="UPWORK_CLIENT_SECRET")
    upwork_refresh_token: str = Field(default="", alias="UPWORK_REFRESH_TOKEN")

    # --- LLM (Groq, бесплатный API, Llama 3.3 70B) --------------------------
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    llm_enabled: bool = Field(default=True, alias="LLM_ENABLED")
    llm_model: str = Field(default="llama-3.3-70b-versatile", alias="LLM_MODEL")
    llm_daily_limit: int = Field(default=800, alias="LLM_DAILY_LIMIT")

    # --- Пороги ------------------------------------------------------------
    min_score_store: int = Field(default=25, alias="MIN_SCORE_STORE")
    min_score_notify: int = Field(default=55, alias="MIN_SCORE_NOTIFY")
    llm_score_low: int = Field(default=35, alias="LLM_SCORE_LOW")
    llm_score_high: int = Field(default=75, alias="LLM_SCORE_HIGH")

    # --- Расписание --------------------------------------------------------
    telegram_poll_minutes: int = Field(default=12, alias="TELEGRAM_POLL_MINUTES")
    jobboard_poll_minutes: int = Field(default=45, alias="JOBBOARD_POLL_MINUTES")
    backfill_days: int = Field(default=3, alias="BACKFILL_DAYS")
    max_messages_per_channel: int = Field(default=120, alias="MAX_MESSAGES_PER_CHANNEL")

    # --- Прочее ------------------------------------------------------------
    database_url: str = Field(
        default="sqlite+aiosqlite:///data/autochecker.db", alias="DATABASE_URL"
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    timezone: str = Field(default="Europe/Warsaw", alias="TIMEZONE")

    @property
    def admin_ids(self) -> list[int]:
        """ADMIN_IDS в .env — «111,222». Нечисловые куски молча пропускаем."""
        ids: list[int] = []
        for chunk in self.admin_ids_raw.replace(" ", "").split(","):
            if chunk.lstrip("-").isdigit():
                ids.append(int(chunk))
        return ids

    @property
    def llm_ready(self) -> bool:
        return self.llm_enabled and bool(self.groq_api_key.strip())

    @property
    def session_path(self) -> Path:
        return DATA_DIR / self.tg_session

    @property
    def use_string_session(self) -> bool:
        return bool(self.tg_session_string.strip())

    @property
    def sources_file(self) -> Path:
        return CONFIG_DIR / "sources.yaml"

    @property
    def upwork_ready(self) -> bool:
        return bool(
            self.upwork_client_id and self.upwork_client_secret and self.upwork_refresh_token
        )


@lru_cache
def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()  # type: ignore[call-arg]
