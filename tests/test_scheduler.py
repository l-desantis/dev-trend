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


def test_scheduler_registers_v4b_jobs() -> None:
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
    # v4.B jobs
    assert "daily_scoring" in job_ids
    assert "daily_digest" in job_ids

    # v3 jobs must be gone
    assert "daily_brief_generation" not in job_ids


def test_scheduler_v4c_jobs_registered() -> None:
    client = httpx.AsyncClient()
    registry = ConnectorRunRegistry()
    connectors = [
        GithubConnector(client, registry),
        HNConnector(client, registry),
        RedditConnector(client, registry),
    ]
    settings = _settings()
    scheduler = build_scheduler(connectors, registry, settings)

    job_ids = {job.id for job in scheduler.get_jobs()}

    assert "playstore_ingestion" in job_ids
    assert "playstore_app_discovery" in job_ids
    assert "weekly_recluster" in job_ids
    # iOS RSS disabled by default
    assert "ios_rss_ingestion" not in job_ids


def test_scheduler_ios_rss_registered_when_enabled() -> None:
    client = httpx.AsyncClient()
    registry = ConnectorRunRegistry()
    connectors = [
        GithubConnector(client, registry),
        HNConnector(client, registry),
        RedditConnector(client, registry),
    ]
    settings = Settings(_env_file=None, enable_ios_rss=True)  # type: ignore[call-arg]
    scheduler = build_scheduler(connectors, registry, settings)

    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "ios_rss_ingestion" in job_ids


def test_reddit_job_uses_configured_interval() -> None:
    client = httpx.AsyncClient()
    registry = ConnectorRunRegistry()
    connectors = [
        GithubConnector(client, registry),
        HNConnector(client, registry),
        RedditConnector(client, registry),
    ]
    settings = Settings(_env_file=None, reddit_cron_interval_hours=24)  # type: ignore[call-arg]
    scheduler = build_scheduler(connectors, registry, settings)

    reddit_job = next(j for j in scheduler.get_jobs() if j.id == "reddit_ingestion")
    assert reddit_job.trigger.interval.total_seconds() == 24 * 3600
