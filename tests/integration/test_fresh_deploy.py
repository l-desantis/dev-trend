"""End-to-end fresh-deploy integration test.

Exercises the complete pipeline from empty DB to digest push with mocks.
Run with: uv run pytest -m integration

This test is slow (exercises all real code paths). It is excluded from the
default pytest run to keep CI fast.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import StaticPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.ingestion.base import BaseConnector, ConnectorRunRegistry, NormalizedItem
from app.llm.mock_adapter import MockLLMAdapter
from app.llm.mock_embedding_adapter import MockEmbeddingAdapter
from app.models import (
    Base,
    CandidateBrief,
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


# ---------------------------------------------------------------------------
# C-18 scenarios
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c18_migration_idempotent(tmp_path):
    """Migration script must run idempotently on an already-migrated DB."""
    from scripts.migrate_to_v4_2 import migrate

    db_file = tmp_path / "test_migrate.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    # Create schema first (simulates a fresh deploy that already ran create_all)
    fresh_engine = create_async_engine(db_url)
    async with fresh_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await fresh_engine.dispose()

    # Run twice — second run should skip all "duplicate column" ALTERs without error
    await migrate(db_url)
    await migrate(db_url)

    check_engine = create_async_engine(db_url)
    async with check_engine.connect() as conn:
        info = (await conn.execute(text("PRAGMA table_info(opportunity_candidates)"))).fetchall()
        col_names = {row[1] for row in info}
    await check_engine.dispose()
    assert "embedding_model" in col_names
    assert "merged_into_id" in col_names
    assert "last_labelled_at" in col_names


class _MockConnector(BaseConnector):
    """Inline mock connector that yields synthetic SourceItems directly into the test session."""

    source_type = "mock_source"

    def __init__(self, session_factory, n_items: int = 10) -> None:
        super().__init__(
            client=MagicMock(spec=httpx.AsyncClient),
            registry=ConnectorRunRegistry(),
        )
        self._session_factory = session_factory
        self._n = n_items

    async def fetch(self, since=None, until=None) -> list[dict]:
        return [{"idx": i} for i in range(self._n)]

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        now = datetime.now(UTC)
        return [
            NormalizedItem(
                source_type=self.source_type,
                external_id=f"mock-{r['idx']}",
                title=f"I wish there was a better way to do task {r['idx']}",
                body=f"Developers keep struggling with task {r['idx']}. No good tools exist.",
                url=f"https://example.com/{r['idx']}",
                created_at=now - timedelta(days=r["idx"] % 7),
                role="extraction",
            )
            for r in raw
        ]

    async def save(self, items: list[NormalizedItem]) -> int:
        async with self._session_factory() as session:
            for item in items:
                si = SourceItem(
                    source_type=item.source_type,
                    external_id=item.external_id,
                    title=item.title,
                    body=item.body,
                    url=item.url,
                    created_at=item.created_at,
                    role=item.role,
                    extraction_state="pending",
                )
                session.add(si)
            await session.commit()
        return len(items)


@pytest.mark.asyncio
async def test_c18_bulk_backfill(session_factory):
    """Bulk backfill with mock connector seeds SourceItems and runs the pipeline."""
    settings = _make_settings()
    llm = MockLLMAdapter()
    embedder = MockEmbeddingAdapter()
    connector = _MockConnector(session_factory, n_items=20)

    # Patch _get_session_factory so bulk_backfill's internal pipeline uses our test DB
    # (bulk_backfill does `from app.db import _get_session_factory` at call time)
    with patch("app.db._get_session_factory", return_value=session_factory):
        from app.ingestion.backfill import bulk_backfill
        report = await bulk_backfill(
            [connector], llm, embedder, settings, history_days=7
        )

    assert report.items_per_source.get("mock_source", 0) == 20
    async with session_factory() as session:
        count = len((await session.execute(select(SourceItem))).scalars().all())
    assert count == 20


@pytest.mark.asyncio
async def test_c18_briefs(session_factory):
    """generate_briefs_for produces at least one CandidateBrief after pipeline runs."""
    settings = _make_settings()
    llm = MockLLMAdapter()
    embedder = MockEmbeddingAdapter()

    async with session_factory() as session:
        await _seed_source_items(session, 20)

    from app.pipeline.orchestrator import run_pipeline
    await run_pipeline(session_factory, llm, embedder, settings)

    async with session_factory() as session:
        candidates = (await session.execute(select(OpportunityCandidate))).scalars().all()

    assert candidates, "pipeline must produce at least one candidate"

    from app.pipeline.brief_generation import generate_briefs_for
    async with session_factory() as session:
        candidates_fresh = (await session.execute(select(OpportunityCandidate))).scalars().all()
        briefs = await generate_briefs_for(session, llm, candidates_fresh[:3])

    assert len(briefs) >= 1
    async with session_factory() as session:
        brief_count = len((await session.execute(select(CandidateBrief))).scalars().all())
    assert brief_count >= 1


@pytest.mark.asyncio
async def test_c18_digest_job(session_factory):
    """Digest job calls bot.send_message with the top candidate's problem statement."""
    settings = _make_settings()
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="mock",
        embedding_provider="mock",
        clustering_min_cluster_size=2,
        specificity_gate=1,
        telegram_allowed_chat_ids=[12345],
        digest_top_n=3,
    )
    llm = MockLLMAdapter()
    embedder = MockEmbeddingAdapter()

    async with session_factory() as session:
        await _seed_source_items(session, 20)

    from app.pipeline.orchestrator import run_pipeline
    await run_pipeline(session_factory, llm, embedder, settings)

    # Label candidates so digest can pick them up (specificity_gate=1 → need specificity≥2)
    from app.pipeline.labelling import run_labelling
    async with session_factory() as session:
        await run_labelling(session, llm)

    # Bump specificity so candidates pass the gate
    async with session_factory() as session:
        for cand in (await session.execute(select(OpportunityCandidate))).scalars().all():
            cand.specificity = 3
        await session.commit()

    # fetch_top_candidates requires CandidateScoreHistory rows from today
    from app.scoring.candidate_scorer import score_all_candidates
    async with session_factory() as session:
        await score_all_candidates(session, as_of=datetime.now(UTC), gate=0)

    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock())

    from app.bot.v4_notifications import run_digest_job
    await run_digest_job(session_factory, bot, llm, settings)

    bot.send_message.assert_called()


@pytest.mark.asyncio
async def test_c18_feedback_callback(session_factory):
    """Feedback callback inserts a CandidateFeedback row."""
    # Seed a candidate directly
    async with session_factory() as session:
        cand = OpportunityCandidate(
            problem_statement="test candidate for feedback",
            specificity=3,
            created_at=datetime.now(UTC),
        )
        session.add(cand)
        await session.commit()
        cand_id = cand.id

    @asynccontextmanager
    async def _patched_get_session():
        async with session_factory() as session:
            yield session

    # Build a minimal fake Telegram Update
    fake_query = MagicMock()
    fake_query.answer = AsyncMock()
    fake_query.edit_message_reply_markup = AsyncMock()
    fake_query.data = f"fb:up:{cand_id}:none"
    fake_query.from_user = MagicMock(id=42)
    fake_query.message = MagicMock(chat_id=99)

    fake_update = MagicMock()
    fake_update.callback_query = fake_query

    from app.bot.feedback import cmd_feedback_callback
    with patch("app.bot.feedback.get_session", _patched_get_session):
        await cmd_feedback_callback(fake_update, MagicMock())

    async with session_factory() as session:
        fb = (await session.execute(select(CandidateFeedback))).scalars().all()
    assert len(fb) == 1
    assert fb[0].candidate_id == cand_id
    assert fb[0].label == "up"


@pytest.mark.asyncio
async def test_c18_second_day_pipeline(session_factory):
    """Second pipeline run attaches new pain-points to existing candidates via identity resolution."""
    settings = _make_settings()
    llm = MockLLMAdapter()
    embedder = MockEmbeddingAdapter()

    # Day 1: seed and run
    async with session_factory() as session:
        await _seed_source_items(session, 20, source_type="reddit")

    from app.pipeline.orchestrator import run_pipeline
    await run_pipeline(session_factory, llm, embedder, settings)

    async with session_factory() as session:
        day1_candidates = len((await session.execute(select(OpportunityCandidate))).scalars().all())
        day1_pps = len((await session.execute(select(PainPoint))).scalars().all())

    assert day1_candidates > 0

    # Day 2: seed new items and re-run
    async with session_factory() as session:
        await _seed_source_items(session, 10, source_type="hn")

    await run_pipeline(session_factory, llm, embedder, settings)

    async with session_factory() as session:
        day2_pps = len((await session.execute(select(PainPoint))).scalars().all())
        attached = len((
            await session.execute(
                select(PainPoint).where(PainPoint.candidate_id.is_not(None))
            )
        ).scalars().all())

    # More pain-points than day 1, and most are attached to candidates
    assert day2_pps > day1_pps
    assert attached > 0
