"""Tests for Settings defaults."""
from pathlib import Path

import pytest

from app.config import Settings


def test_config_v4_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    s = Settings()
    assert s.llm_provider == "ollama"
    assert s.embedding_provider == "ollama"
    assert s.nim_api_key == ""
    assert s.nim_llm_model == "meta/llama-3.1-70b-instruct"
    assert s.nim_embedding_model == "nvidia/nv-embedqa-e5-v5"
    assert s.extraction_batch_size == 20
    assert s.embedding_batch_size == 64
    assert s.identity_resolution_threshold == 0.82
    assert s.clustering_min_cluster_size == 3
    assert s.specificity_gate == 2
    assert s.max_alerts_per_day == 3
    assert s.pipeline_cron_hour == 3
    assert s.pipeline_cron_minute == 30


def test_env_example_covers_required_settings() -> None:
    env_path = Path(__file__).parent.parent / ".env.example"
    env_keys = {
        line.split("=")[0].strip().upper()
        for line in env_path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }
    required = {
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS",
        "LLM_PROVIDER", "EMBEDDING_PROVIDER",
        "NIM_API_KEY", "NIM_BASE_URL", "NIM_LLM_MODEL", "NIM_EMBEDDING_MODEL",
        "PLAYSTORE_CRON_HOUR", "PLAYSTORE_TOP_N_PER_CATEGORY", "PLAYSTORE_REVIEWS_PER_APP",
        "ENABLE_IOS_RSS",
        "WEEKLY_RECLUSTER_CRON_HOUR", "WEEKLY_RECLUSTER_CRON_DAY",
        "IDENTITY_RESOLUTION_THRESHOLD",
    }
    missing = required - env_keys
    assert not missing, f"missing in .env.example: {missing}"
