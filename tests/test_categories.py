"""Tests for sync_categories_from_yaml."""
import textwrap
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select

from app.db_helpers.categories import sync_categories_from_yaml
from app.models import Base, Category


@pytest.fixture
async def session(tmp_path: Path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def categories_yaml(tmp_path: Path) -> Path:
    content = textwrap.dedent("""\
        categories:
          - slug: wellness
            name: Wellness & Mental Health
            description: Apps for habit, fitness, sleep.
          - slug: finance
            name: Personal Finance
            description: Budgeting and tracking.
          - slug: devtools
            name: Developer Tools
            description: Code review and CI/CD.
          - slug: productivity
            name: Productivity & Knowledge
            description: PKM and writing.
          - slug: creative
            name: Creative & Media
            description: AI-assisted media.
          - slug: gaming
            name: Gaming & Indie Games
            description: Game engines.
    """)
    p = tmp_path / "categories.yaml"
    p.write_text(content)
    return p


async def test_sync_categories_inserts_new(session: AsyncSession, categories_yaml: Path) -> None:
    await sync_categories_from_yaml(session, path=categories_yaml)
    rows = (await session.execute(select(Category))).scalars().all()
    assert len(rows) == 6
    slugs = {r.slug for r in rows}
    assert "wellness" in slugs
    assert "gaming" in slugs


async def test_sync_categories_updates_existing_by_slug(
    session: AsyncSession, tmp_path: Path
) -> None:
    # Pre-seed with old name
    session.add(Category(slug="wellness", name="Old Name", description="old"))
    await session.commit()

    yaml_path = tmp_path / "cats.yaml"
    yaml_path.write_text(
        "categories:\n  - slug: wellness\n    name: Wellness & Mental Health\n    description: new\n"
    )
    await sync_categories_from_yaml(session, path=yaml_path)

    cat = (await session.execute(select(Category).where(Category.slug == "wellness"))).scalar_one()
    assert cat.name == "Wellness & Mental Health"
    assert cat.description == "new"


async def test_sync_categories_does_not_delete_missing(
    session: AsyncSession, tmp_path: Path
) -> None:
    # Pre-seed a row that will NOT be in the YAML
    session.add(Category(slug="legacy_slug", name="Legacy", description="old"))
    await session.commit()

    yaml_path = tmp_path / "cats.yaml"
    yaml_path.write_text(
        "categories:\n  - slug: wellness\n    name: Wellness\n    description: new\n"
    )
    await sync_categories_from_yaml(session, path=yaml_path)

    legacy = (
        await session.execute(select(Category).where(Category.slug == "legacy_slug"))
    ).scalar_one_or_none()
    assert legacy is not None
