from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    app_name: str = "DevTrend"
    env: str = "dev"
    version: str = "0.1.0"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_allowed_chat_ids: list[int] = []

    # LLM
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5"

    # Database
    database_url: str = "sqlite+aiosqlite:///./devtrend.db"

    # Data Sources
    github_token: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "DevTrend/1.0 (by /u/yourhandle)"
    enable_mock_appstore: bool = True

    # Scheduling
    daily_digest_time: str = "08:00"
    spike_alert_threshold: float = 15.0

    # Logging
    log_level: str = "INFO"

    @field_validator("telegram_allowed_chat_ids", mode="before")
    @classmethod
    def parse_chat_ids(cls, v: object) -> list[int]:
        if not v:
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [int(x) for x in v]
        return []


@lru_cache
def get_settings() -> Settings:
    return Settings()
