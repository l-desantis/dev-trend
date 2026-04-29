"""Tests for the v4 scheduler configuration."""
import httpx
import pytest

from app.config import Settings
from app.ingestion.base import ConnectorRunRegistry
from app.ingestion.github_connector import GithubConnector
from app.ingestion.hn_connector import HNConnector
from app.ingestion.reddit_connector import RedditConnector
from app.ingestion.scheduler import build_scheduler


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_scheduler_registers_v4_jobs() -> None:
    client = httpx.AsyncClient()
    registry = ConnectorRunRegistry()
    connectors = [
        GithubConnector(client, registry),
        HNConnector(client, registry),
        RedditConnector(client, registry),
    ]
    scheduler = build_scheduler(connectors, registry, _settings())

    job_ids = {job.id for job in scheduler.get_jobs()}

    assert "daily_pipeline" in job_ids
    assert "weekly_pruning" in job_ids
    assert "github_ingestion" in job_ids
    assert "hn_ingestion" in job_ids
    assert "reddit_ingestion" in job_ids

    # v3 jobs must be gone
    assert "daily_scoring" not in job_ids
    assert "daily_brief_generation" not in job_ids
    assert "daily_digest" not in job_ids
