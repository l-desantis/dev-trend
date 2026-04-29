"""Tests for the pipeline orchestrator."""
import pytest
from datetime import UTC, datetime, timedelta
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.llm.mock_adapter import MockLLMAdapter
from app.llm.mock_embedding_adapter import MockEmbeddingAdapter
from app.models import Base, OpportunityCandidate, PainPoint, SourceItem
from app.pipeline.orchestrator import run_pipeline


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _settings(**kwargs) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="mock",
        embedding_provider="mock",
        clustering_min_cluster_size=2,
        **kwargs,
    )


async def _seed_items(factory: async_sessionmaker, items: list[dict]) -> None:
    async with factory() as session:
        for d in items:
            session.add(SourceItem(**d))
        await session.commit()


async def test_orchestrator_runs_all_stages_in_order(session_factory: async_sessionmaker) -> None:
    # Seed 3 high-signal items so clustering can form a cluster (min_size=2)
    await _seed_items(session_factory, [
        {"source_type": "reddit", "external_id": f"t3_{i}", "role": "extraction",
         "title": f"I wish there was a habit tracker for ADHD {i}"}
        for i in range(3)
    ])

    report = await run_pipeline(
        session_factory, MockLLMAdapter(), MockEmbeddingAdapter(), _settings()
    )

    assert report.extraction is not None
    assert report.extraction.painpoints_created >= 1
    assert report.clustering is not None
    assert report.labelling is not None


async def test_orchestrator_respects_since(session_factory: async_sessionmaker) -> None:
    old_time = datetime.now(UTC) - timedelta(days=5)
    recent_time = datetime.now(UTC)

    async with session_factory() as session:
        # Old item — should be skipped
        item_old = SourceItem(
            source_type="reddit", external_id="t3_old",
            title="I wish there was an app that tracks old habit",
            role="extraction",
        )
        session.add(item_old)
        await session.commit()
        # Manually set ingested_at to old time
        await session.execute(
            update(SourceItem)
            .where(SourceItem.external_id == "t3_old")
            .values(ingested_at=old_time)
        )
        # Recent item
        item_new = SourceItem(
            source_type="reddit", external_id="t3_new",
            title="Why is there no way to track habits for ADHD adults?",
            role="extraction",
        )
        session.add(item_new)
        await session.commit()

    since = datetime.now(UTC) - timedelta(days=1)
    report = await run_pipeline(
        session_factory, MockLLMAdapter(), MockEmbeddingAdapter(), _settings(), since=since
    )

    # Only the recent item should be processed
    assert report.extraction.processed <= 1


async def test_orchestrator_idempotent(session_factory: async_sessionmaker) -> None:
    await _seed_items(session_factory, [
        {"source_type": "reddit", "external_id": f"t3_idem_{i}", "role": "extraction",
         "title": f"I wish there was a better finance app {i}"}
        for i in range(3)
    ])

    await run_pipeline(
        session_factory, MockLLMAdapter(), MockEmbeddingAdapter(), _settings()
    )
    report2 = await run_pipeline(
        session_factory, MockLLMAdapter(), MockEmbeddingAdapter(), _settings()
    )

    # Second run should not create new PainPoints
    assert report2.extraction.painpoints_created == 0
    assert report2.clustering.candidates_created == 0
