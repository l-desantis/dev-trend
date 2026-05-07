"""Tests for weekly re-cluster pass and candidate_resolution helper."""
import pytest
from datetime import UTC, datetime, timedelta

from sqlalchemy import StaticPool, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, OpportunityCandidate, PainPoint, SourceItem
from app.pipeline.recluster import run_weekly_recluster
from app.db_helpers.candidate_resolution import resolve_candidate_root


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


async def _seed_source_item(session: AsyncSession, idx: int) -> SourceItem:
    si = SourceItem(
        source_type="reddit",
        external_id=f"si-{idx}",
        role="extraction",
        extraction_state="pending",
    )
    session.add(si)
    await session.flush()
    return si


async def _seed_candidate(
    session: AsyncSession,
    centroid: list[float],
    emb_model: str = "ollama:nomic-embed-text",
) -> OpportunityCandidate:
    c = OpportunityCandidate(
        problem_statement="test cluster",
        centroid=centroid,
        embedding_model=emb_model,
        specificity=3,
        created_at=datetime.now(UTC),
    )
    session.add(c)
    await session.flush()
    return c


async def _seed_pain_point(
    session: AsyncSession,
    source_item_id: int,
    embedding: list[float],
    candidate_id: int | None = None,
    emb_model: str = "ollama:nomic-embed-text",
) -> PainPoint:
    pp = PainPoint(
        source_item_id=source_item_id,
        extractor_model="mock",
        embedding=embedding,
        embedding_model=emb_model,
        candidate_id=candidate_id,
        extracted_at=datetime.now(UTC),
    )
    session.add(pp)
    await session.flush()
    return pp


# -------------------------------------------------------------------------
# resolve_candidate_root tests
# -------------------------------------------------------------------------

async def test_resolve_candidate_root_walks_chain(session: AsyncSession) -> None:
    c1 = OpportunityCandidate(problem_statement="c1", created_at=datetime.now(UTC), is_archived=True)
    c2 = OpportunityCandidate(problem_statement="c2", created_at=datetime.now(UTC), is_archived=True)
    c3 = OpportunityCandidate(problem_statement="c3", created_at=datetime.now(UTC), is_archived=False)
    session.add_all([c1, c2, c3])
    await session.flush()
    c1.merged_into_id = c2.id
    c2.merged_into_id = c3.id
    await session.commit()

    root = await resolve_candidate_root(session, c1.id)
    assert root == c3.id


async def test_resolve_candidate_root_detects_cycle(session: AsyncSession) -> None:
    c1 = OpportunityCandidate(problem_statement="c1", created_at=datetime.now(UTC), is_archived=True)
    c2 = OpportunityCandidate(problem_statement="c2", created_at=datetime.now(UTC), is_archived=True)
    session.add_all([c1, c2])
    await session.flush()
    c1.merged_into_id = c2.id
    c2.merged_into_id = c1.id
    await session.commit()

    with pytest.raises(RuntimeError, match="cycle"):
        await resolve_candidate_root(session, c1.id)


# -------------------------------------------------------------------------
# Re-cluster tests
# -------------------------------------------------------------------------

async def test_recluster_does_nothing_when_clusters_stable(session: AsyncSession) -> None:
    """Coherent clusters: 0 merges, 0 splits."""
    si1 = await _seed_source_item(session, 1)
    si2 = await _seed_source_item(session, 2)
    # Two well-separated candidates
    c1 = await _seed_candidate(session, [1.0, 0.0, 0.0])
    c2 = await _seed_candidate(session, [0.0, 1.0, 0.0])
    # Pain-points tightly around their own candidate
    for i in range(3):
        await _seed_pain_point(session, si1.id, [0.98, 0.02, 0.0], candidate_id=c1.id)
        await _seed_pain_point(session, si2.id, [0.02, 0.98, 0.0], candidate_id=c2.id)
    await session.commit()

    report = await run_weekly_recluster(session, merge_threshold=0.88, min_cluster_size=2)
    assert report.merged_count == 0
    assert report.split_count == 0


async def test_recluster_merges_drifted_candidates(session: AsyncSession) -> None:
    """Two candidates whose pain-points converge should be merged."""
    si = await _seed_source_item(session, 10)
    # Both candidates have very similar centroids
    c1 = await _seed_candidate(session, [1.0, 0.0, 0.0])
    c2 = await _seed_candidate(session, [0.99, 0.14, 0.0])
    # Pain-points all close together
    for i in range(4):
        await _seed_pain_point(session, si.id, [1.0, 0.0, 0.0], candidate_id=c1.id)
        await _seed_pain_point(session, si.id, [0.99, 0.14, 0.0], candidate_id=c2.id)
    await session.commit()

    report = await run_weekly_recluster(session, merge_threshold=0.85, min_cluster_size=2)
    assert report.merged_count >= 1

    # One candidate should be archived with merged_into_id set
    archived = (
        await session.execute(
            select(OpportunityCandidate).where(OpportunityCandidate.is_archived.is_(True))
        )
    ).scalars().all()
    assert len(archived) == 1
    assert archived[0].merged_into_id is not None


async def test_recluster_splits_overbroad_candidate(session: AsyncSession) -> None:
    """Overbroad candidate spanning two sub-clusters should be split."""
    si = await _seed_source_item(session, 30)
    # Centroid between the two future sub-clusters
    cand = await _seed_candidate(session, [0.707, 0.707, 0.0])
    # 3 pain-points near [1,0,0] and 3 near [0,1,0] — orthogonal groups
    for _ in range(3):
        await _seed_pain_point(session, si.id, [1.0, 0.0, 0.0], candidate_id=cand.id)
        await _seed_pain_point(session, si.id, [0.0, 1.0, 0.0], candidate_id=cand.id)
    await session.commit()

    # threshold=0.75 > cohesion≈0.707, so the split fires; min_cluster_size=3 → n_clusters=2
    report = await run_weekly_recluster(
        session,
        merge_threshold=0.99,
        split_silhouette_threshold=0.75,
        min_cluster_size=3,
    )

    assert report.split_count >= 1

    all_cands = (await session.execute(select(OpportunityCandidate))).scalars().all()
    assert len(all_cands) >= 2  # original + at least one new

    # Original candidate retains the largest sub-cluster; new one gets the rest
    original_pp_count = sum(
        1 for pp in (await session.execute(select(PainPoint))).scalars().all()
        if pp.candidate_id == cand.id
    )
    assert original_pp_count == 3


async def test_recluster_filters_by_embedding_model(session: AsyncSession) -> None:
    """Pain points with different embedding_models must not be co-clustered."""
    si = await _seed_source_item(session, 20)
    c_ollama = await _seed_candidate(session, [1.0, 0.0, 0.0], emb_model="ollama:nomic-embed-text")
    c_nim = await _seed_candidate(session, [1.0, 0.0, 0.0], emb_model="nim:nvidia/nv-embedqa-e5-v5")

    for _ in range(4):
        await _seed_pain_point(session, si.id, [1.0, 0.0, 0.0], candidate_id=c_ollama.id, emb_model="ollama:nomic-embed-text")
        await _seed_pain_point(session, si.id, [1.0, 0.0, 0.0], candidate_id=c_nim.id, emb_model="nim:nvidia/nv-embedqa-e5-v5")
    await session.commit()

    # Run with high threshold — ollama cluster and nim cluster should NOT be merged together
    # since they are processed in separate embedding_model groups
    report = await run_weekly_recluster(session, merge_threshold=0.9, min_cluster_size=2)
    # Check that neither was cross-model merged
    c_ollama_fresh = await session.get(OpportunityCandidate, c_ollama.id)
    c_nim_fresh = await session.get(OpportunityCandidate, c_nim.id)
    # Both may be merged within their own model group but not across models
    if c_ollama_fresh and c_ollama_fresh.is_archived:
        assert c_ollama_fresh.merged_into_id != c_nim.id
    if c_nim_fresh and c_nim_fresh.is_archived:
        assert c_nim_fresh.merged_into_id != c_ollama.id
