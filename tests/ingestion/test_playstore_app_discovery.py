"""Tests for Play Store app discovery (static seed loader)."""
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ingestion.playstore_app_discovery import AppListing, load_seed_apps, refresh_app_list
from app.models import TrackedApp


@pytest.fixture
def tmp_seed(tmp_path: Path) -> Path:
    seed = tmp_path / "seed.yaml"
    seed.write_text(
        textwrap.dedent("""\
        apps:
          - app_id: com.example.a
            title: "App A"
            internal_category: wellness
          - app_id: com.example.b
            title: "App B"
            internal_category: finance
          - app_id: com.example.a
            title: "App A dup"
            internal_category: wellness
        """),
        encoding="utf-8",
    )
    return seed


@pytest.fixture
async def session(database_url: str) -> AsyncSession:
    engine = create_async_engine(database_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        try:
            yield s
        finally:
            await s.rollback()
    await engine.dispose()


def test_load_seed_apps_from_yaml(tmp_seed: Path) -> None:
    apps = load_seed_apps(tmp_seed)
    assert len(apps) == 3
    assert all(isinstance(a, AppListing) for a in apps)
    assert apps[0].app_id == "com.example.a"


async def test_refresh_app_list_upserts(tmp_seed: Path, session: AsyncSession) -> None:
    count = await refresh_app_list(session, seed_path=tmp_seed)
    assert count == 3
    rows = (await session.execute(select(TrackedApp))).scalars().all()
    # Duplicate app_id: last write wins (upsert), expect 2 unique rows
    assert len(rows) == 2


async def test_refresh_app_list_updates_last_seen_at(tmp_seed: Path, session: AsyncSession) -> None:
    # Seed with old timestamp
    old = TrackedApp(
        app_id="com.example.a",
        title="Old Title",
        internal_category="wellness",
        last_seen_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(old)
    await session.commit()

    await refresh_app_list(session, seed_path=tmp_seed)

    refreshed = await session.get(TrackedApp, "com.example.a")
    assert refreshed is not None
    assert refreshed.last_seen_at > datetime(2020, 1, 2, tzinfo=UTC)
