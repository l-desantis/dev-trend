"""Shared test fixtures."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import reset_engine


@pytest.fixture(scope="session")
def database_url() -> str:
    """Return a Postgres URL for the test session.

    In CI we read DATABASE_URL from the env (a service container). Locally we
    start a Postgres container via testcontainers so developers don't need to
    manage one themselves.
    """
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    container.start()

    import atexit
    atexit.register(container.stop)

    raw = container.get_connection_url()  # postgresql+psycopg2://...
    return raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(database_url: str) -> None:
    """Run `alembic upgrade head` once per test session."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    os.environ["DATABASE_URL"] = database_url
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
async def _clean_db(database_url: str) -> None:
    """Truncate all data before each test for isolation."""
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE TABLE candidate_feedback, pain_points, lifecycle_events, "
            "candidate_validations, candidate_score_history, candidate_briefs, "
            "source_items, opportunity_candidates, tracked_apps, maintenance_state, categories "
            "RESTART IDENTITY CASCADE"
        ))
    await engine.dispose()


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # DATABASE_URL is set at session scope by _apply_migrations; don't override it.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    monkeypatch.setenv("BACKFILL_ON_EMPTY", "false")
    get_settings.cache_clear()
    reset_engine()


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """A fresh AsyncSession per test."""
    engine = create_async_engine(database_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        try:
            yield s
        finally:
            await s.rollback()
    await engine.dispose()


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
