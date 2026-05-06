"""End-to-end fresh-deploy integration test.

Exercises the complete pipeline from empty DB to digest push with mocks.
Run with: uv run pytest -m integration

This test is slow (exercises all real code paths). It is excluded from the
default pytest run to keep CI fast.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import StaticPool, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.llm.mock_adapter import MockLLMAdapter
from app.llm.mock_embedding_adapter import MockEmbeddingAdapter
from app.models import (
    Base,
    CandidateFeedback,
    CandidateScoreHistory,
    OpportunityCandidate,
    PainPoint,
    SourceItem,
    TrackedApp,
)

pytestmark = pytest.mark.integration


def _make_settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="mock",
        embedding_provider="mock",
        clustering_min_cluster_size=2,
        specificity_gate=1,
        extraction_batch_size=100,
        embedding_batch_size=64,
        identity_resolution_threshold=0.5,
    )


@pytest.fixture
async def engine():
    e = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_source_items(session: AsyncSession, n: int, source_type: str = "reddit") -> None:
    now = datetime.now(UTC)
    for i in range(n):
        si = SourceItem(
            source_type=source_type,
            external_id=f"item-{source_type}-{i}",
            title=f"I wish there was a way to do X ({i})",
            body=f"This is really frustrating. Why is there no good tool for task {i}?",
            url=f"https://example.com/{i}",
            created_at=now - timedelta(days=i % 7),
            role="extraction",
            extraction_state="pending",
        )
        session.add(si)
    await session.commit()


@pytest.mark.asyncio
async def test_fresh_deploy_pipeline(session_factory):
    """Full pipeline on 30 synthetic source items with mocked LLM + embeddings."""
    settings = _make_settings()
    llm = MockLLMAdapter()
    embedder = MockEmbeddingAdapter()

    # Step 1: seed source items (simulates backfill)
    async with session_factory() as session:
        await _seed_source_items(session, 30)

    # Step 2: run pipeline
    from app.pipeline.orchestrator import run_pipeline
    report = await run_pipeline(session_factory, llm, embedder, settings)

    assert report.extraction is not None
    assert report.clustering is not None

    # Step 3: verify candidates + score history exist
    async with session_factory() as session:
        candidates = (await session.execute(select(OpportunityCandidate))).scalars().all()
        assert len(candidates) > 0

    # Step 4: run scoring
    from app.scoring.candidate_scorer import score_all_candidates
    async with session_factory() as session:
        await score_all_candidates(session, as_of=datetime.now(UTC), gate=0)

    async with session_factory() as session:
        scores = (await session.execute(select(CandidateScoreHistory))).scalars().all()
        assert len(scores) > 0


@pytest.mark.asyncio
async def test_fresh_deploy_playstore_ingestion(session_factory):
    """Play Store ingestion with mocked google_play_scraper produces extraction-role SourceItems."""
    import httpx
    from app.ingestion.base import ConnectorRunRegistry
    from app.ingestion.playstore_connector import PlayStoreReviewsConnector

    # Seed a tracked app
    async with session_factory() as session:
        session.add(TrackedApp(
            app_id="com.example.app",
            title="Example App",
            internal_category="wellness",
        ))
        await session.commit()

    fake_review = {
        "reviewId": "rev-001",
        "userName": "User",
        "userImage": "",
        "content": "I wish this app had a better onboarding flow",
        "score": 3,
        "thumbsUpCount": 10,
        "reviewCreatedVersion": "1.0",
        "at": datetime.now(UTC),
        "replyContent": None,
        "repliedAt": None,
        "appVersion": "1.0.0",
    }

    connector = PlayStoreReviewsConnector(
        client=MagicMock(spec=httpx.AsyncClient),
        registry=ConnectorRunRegistry(),
    )

    with (
        patch("app.ingestion.playstore_connector.get_session") as mock_gs,
        patch("app.ingestion.playstore_connector.asyncio.to_thread") as mock_thread,
        patch("app.ingestion.playstore_connector.asyncio.sleep", new=AsyncMock()),
    ):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        tracked_app = MagicMock()
        tracked_app.app_id = "com.example.app"
        mock_session.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[tracked_app])))
            )
        )
        mock_gs.return_value = mock_session
        mock_thread.return_value = ([fake_review], None)

        raw = await connector.fetch()

    assert len(raw) == 1
    items = connector.normalize(raw)
    assert len(items) == 1
    assert items[0].source_type == "playstore"
    assert items[0].role == "extraction"


@pytest.mark.asyncio
async def test_fresh_deploy_recluster_stable(session_factory):
    """Re-cluster on coherent data: 0 merges, 0 splits."""
    from app.pipeline.recluster import run_weekly_recluster

    async with session_factory() as session:
        si = SourceItem(
            source_type="reddit", external_id="si-rc-1",
            role="extraction", extraction_state="pending",
        )
        session.add(si)
        await session.flush()

        c = OpportunityCandidate(
            problem_statement="stable cluster",
            centroid=[1.0, 0.0, 0.0],
            embedding_model="mock",
            created_at=datetime.now(UTC),
        )
        session.add(c)
        await session.flush()

        for i in range(3):
            session.add(PainPoint(
                source_item_id=si.id,
                extractor_model="mock",
                embedding=[1.0, 0.0, 0.0],
                embedding_model="mock",
                candidate_id=c.id,
                extracted_at=datetime.now(UTC),
            ))
        await session.commit()

    async with session_factory() as session:
        report = await run_weekly_recluster(session, min_cluster_size=2)

    assert report.merged_count == 0
    assert report.split_count == 0
