import json as _json
from functools import lru_cache
from pathlib import Path
from typing import Any

from typing import Literal

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
    database_url: str = "postgresql+asyncpg://devtrend:devtrend@postgres:5432/devtrend"

    # Data Sources
    github_token: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "devtrend/1.0"
    # Ingestion behavior
    reddit_subreddits: list[str] = [
        "startups",
    "SideProject",
    "Entrepreneur",
    "androiddev",
    "iOSProgramming",
    "AppIdeas",
    "SomebodyMakeThis",
    "nocode",
    "automation",
    "smallbusiness",
    "webdev",
    "gamedev",
    "AppDevelopers",
    "ProductivityApps",
    "ADHD",
    ]
    # Minimum spacing between *any* two Reddit HTTP calls (between subs in the
    # scheduled path AND between pagination pages in backfill). 6.0s = 60s / 10
    # req → enforces the ≤10 req/min ceiling globally for the connector.
    reddit_delay_seconds: float = 6.0
    reddit_cron_interval_hours: int = 12
    reddit_max_subreddits_per_run: int | None = None  # None = all
    github_star_threshold: int = 50
    github_search_lookback_days: int = 14
    ingestion_http_timeout_s: float = 20.0
    ingestion_job_timeout_s: float = 180.0

    # Daily digest push
    digest_cron_hour: int = 8
    digest_cron_minute: int = 0
    digest_top_n: int = 3

    # Spike alerts
    spike_alert_threshold: float = 15.0

    # /trending command
    trending_top_n: int = 5
    trending_window_hours: int = 24

    # /briefing command
    briefing_top_n: int = 3

    # Telegram message limit
    telegram_max_message_chars: int = 4096

    # Scoring
    growth_weight: float = 0.41
    demand_weight: float = 0.35
    novelty_weight: float = 0.24
    scoring_growth_window_days: int = 7
    scoring_novelty_max_age_days: int = 30
    scoring_normalization_window_days: int = 30
    scoring_cron_hour: int = 4
    scoring_cron_minute: int = 15

    # Agent / brief generation
    llm_provider: Literal["ollama", "nim", "mock", "openai"] = "ollama"
    embedding_provider: Literal["ollama", "nim", "mock", "openai"] = "ollama"
    brief_cron_hour: int = 3
    brief_cron_minute: int = 0
    brief_per_niche_timeout_s: float = 90.0
    brief_max_evidence_items: int = 5
    brief_min_summary_chars: int = 50

    # NIM (NVIDIA)
    nim_api_key: str = ""
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_llm_model: str = "meta/llama-3.1-70b-instruct"
    nim_embedding_model: str = "nvidia/nv-embedqa-e5-v5"
    nim_rate_limit_enabled: bool = True
    nim_rate_limit_rpm: int = 40

    # OpenAI
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_llm_model: str = "gpt-4.1-nano"
    openai_embedding_model: str = "text-embedding-3-small"

    # Play Store
    playstore_cron_hour: int = 2

    # iOS RSS (optional)
    enable_ios_rss: bool = False

    # v4 pipeline settings
    extraction_batch_size: int = 20
    embedding_batch_size: int = 64
    identity_resolution_threshold: float = 0.82
    clustering_min_cluster_size: int = 3
    specificity_gate: int = 2
    max_alerts_per_day: int = 3

    pipeline_cron_hour: int = 3
    pipeline_cron_minute: int = 30

    weekly_recluster_cron_hour: int = 4
    weekly_recluster_cron_day: str = "sun"

    playstore_top_n_per_category: int = 50
    playstore_reviews_per_app: int = 200

    # Bulk backfill (runs once on startup when DB is empty)
    backfill_on_empty: bool = False
    backfill_history_days: int = 30
    backfill_max_items_per_source: int = 1000

    # Pruning
    source_retention_days: int = 90
    signal_retention_days: int = 30
    pruning_cron_hour: int = 3

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
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _CommaSepEnvSource(settings_cls),
            _CommaSepDotEnvSource(settings_cls),
            file_secret_settings,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
