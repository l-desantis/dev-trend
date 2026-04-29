"""Tests for Settings defaults."""
import pytest

from app.config import Settings


def test_config_v4_defaults() -> None:
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
    )
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
