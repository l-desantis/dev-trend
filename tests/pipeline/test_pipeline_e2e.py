"""End-to-end pipeline fixture test."""
import pytest
import numpy as np
from pathlib import Path
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy import select

from app.config import Settings
from app.db_helpers.categories import sync_categories_from_yaml
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


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="mock",
        embedding_provider="mock",
        clustering_min_cluster_size=2,
        extraction_batch_size=50,
        embedding_batch_size=50,
        identity_resolution_threshold=0.95,  # high threshold → no merging with existing
    )


async def test_full_pipeline_e2e(session_factory: async_sessionmaker, tmp_path: Path) -> None:
    # 1. Sync 6 categories
    cats_yaml = tmp_path / "categories.yaml"
    cats_yaml.write_text(
        "categories:\n"
        + "\n".join(
            f"  - slug: cat{i}\n    name: Cat{i}\n    description: d{i}"
            for i in range(6)
        )
    )
    async with session_factory() as session:
        await sync_categories_from_yaml(session, path=cats_yaml)

    # 2. Insert ~20 fixture SourceItems
    async with session_factory() as session:
        # 8 Reddit high-signal
        for i in range(8):
            session.add(SourceItem(
                source_type="reddit",
                external_id=f"reddit_high_{i}",
                title=f"I wish there was a habit tracker for ADHD {i}",
                role="extraction",
            ))
        # 4 Reddit no-signal
        for i in range(4):
            session.add(SourceItem(
                source_type="reddit",
                external_id=f"reddit_low_{i}",
                title=f"Tech company announces new product {i}",
                role="extraction",
            ))
        # 3 HN Show (validation, not extracted)
        for i in range(3):
            session.add(SourceItem(
                source_type="hn",
                external_id=f"hn_show_{i}",
                title=f"Show HN: My new tool {i}",
                role="validation",
            ))
        # 3 HN Ask (extraction)
        for i in range(3):
            session.add(SourceItem(
                source_type="hn",
                external_id=f"hn_ask_{i}",
                title=f"Ask HN: Why is there no good productivity app {i}?",
                role="extraction",
            ))
        # 2 GitHub (validation)
        for i in range(2):
            session.add(SourceItem(
                source_type="github",
                external_id=f"gh_{i}",
                title=f"owner/repo-{i}",
                role="validation",
            ))
        await session.commit()

    # 3. Run pipeline
    settings = _settings()
    report = await run_pipeline(
        session_factory,
        MockLLMAdapter(),
        MockEmbeddingAdapter(),
        settings,
    )

    # 4. Assertions
    async with session_factory() as session:
        pps = (await session.execute(select(PainPoint))).scalars().all()
        candidates = (await session.execute(select(OpportunityCandidate))).scalars().all()
        validation_items = (
            await session.execute(
                select(SourceItem).where(SourceItem.role == "validation")
            )
        ).scalars().all()
        validation_pps = (
            await session.execute(
                select(PainPoint).where(
                    PainPoint.source_item_id.in_([i.id for i in validation_items])
                )
            )
        ).scalars().all()

    # 11 high-signal items: 8 Reddit + 3 Ask HN
    assert len(pps) == 11, f"Expected 11 PainPoints, got {len(pps)}"

    # Validation-role items have no PainPoints
    assert len(validation_pps) == 0

    # At least 2 candidates labelled with real problem statements
    labelled = [c for c in candidates if c.labeller_model is not None]
    assert len(labelled) >= 2, f"Expected ≥2 labelled candidates, got {len(labelled)}"
    for c in labelled:
        assert c.problem_statement != ""
        assert c.specificity >= 1

    # 5. Idempotency: second run produces no new PainPoints
    report2 = await run_pipeline(
        session_factory,
        MockLLMAdapter(),
        MockEmbeddingAdapter(),
        settings,
    )
    assert report2.extraction.painpoints_created == 0
    assert report2.clustering.candidates_created == 0
