import json as _json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict
from pydantic_settings import DotEnvSettingsSource, PydanticBaseSettingsSource


class _CommaSepMixin:
    """Mixin: fall back to comma-split for list-type env vars that aren't valid JSON."""

    def decode_complex_value(self, field_name: str, field: FieldInfo, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return _json.loads(value)
            except _json.JSONDecodeError:
                return [v.strip() for v in value.split(",") if v.strip()]
        return super().decode_complex_value(field_name, field, value)  # type: ignore[misc]


class _CommaSepEnvSource(_CommaSepMixin, EnvSettingsSource):
    pass


class _CommaSepDotEnvSource(_CommaSepMixin, DotEnvSettingsSource):
    pass


_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
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

    # Ingestion behavior
    reddit_subreddits: list[str] = [
        "startups", "SideProject", "Entrepreneur",
        "reactnative", "androiddev", "iOSProgramming",
        "AppIdeas"
    ]
    github_star_threshold: int = 50
    github_search_lookback_days: int = 14
    ingestion_http_timeout_s: float = 20.0
    ingestion_job_timeout_s: float = 180.0

    # Scheduling
    daily_digest_time: str = "08:00"
    spike_alert_threshold: float = 15.0

    # Scoring
    growth_weight: float = 0.41
    demand_weight: float = 0.35
    novelty_weight: float = 0.24
    scoring_growth_window_days: int = 7
    scoring_novelty_max_age_days: int = 30
    scoring_normalization_window_days: int = 30
    scoring_cron_hour: int = 2
    scoring_cron_minute: int = 15

    # Logging
    log_level: str = "INFO"

    @field_validator("reddit_subreddits", mode="before")
    @classmethod
    def parse_subreddits(cls, v: object) -> list[str]:
        if not v:
            return []
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [str(x) for x in v]
        return []

    @field_validator("telegram_allowed_chat_ids", mode="before")
    @classmethod
    def parse_chat_ids(cls, v: object) -> list[int]:
        if not v and v != 0:
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, (int, float)):
            return [int(v)]
        if isinstance(v, list):
            return [int(x) for x in v]
        return []

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        **kwargs: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _CommaSepEnvSource(settings_cls),
            _CommaSepDotEnvSource(settings_cls),
            *kwargs.values(),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
