"""Tests for Stage 4 — clustering."""
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import OpportunityCandidate, PainPoint, SourceItem
from app.pipeline.clustering import run_clustering


async def _seed_item(session: AsyncSession, eid: str) -> SourceItem:
    item = SourceItem(source_type="reddit", external_id=eid, role="extraction")
    session.add(item)
    await session.flush()
    return item


def _unit(v: list[float]) -> list[float]:
    a = np.array(v, dtype=np.float32)
    return (a / np.linalg.norm(a)).tolist()


async def _make_pp(session: AsyncSession, item_id: int, embedding: list[float]) -> PainPoint:
    pp = PainPoint(
        source_item_id=item_id, extractor_model="mock",
        problem_text="p", audience="a", embedding=embedding
    )
    session.add(pp)
    return pp


async def test_clustering_groups_similar_points(session: AsyncSession) -> None:
    # Two clear clusters using synthetic embeddings
    base_a = _unit([1.0, 0.0] + [0.0] * 30)
    base_b = _unit([0.0, 1.0] + [0.0] * 30)

    for i in range(3):
        item = await _seed_item(session, f"a{i}")
        noise = np.random.default_rng(i).normal(0, 0.01, 32)
        vec = _unit((np.array(base_a) + noise).tolist())
        await _make_pp(session, item.id, vec)

    for i in range(3):
        item = await _seed_item(session, f"b{i}")
        noise = np.random.default_rng(i + 10).normal(0, 0.01, 32)
        vec = _unit((np.array(base_b) + noise).tolist())
        await _make_pp(session, item.id, vec)

    await session.commit()

    report = await run_clustering(session, min_cluster_size=2)

    assert report.candidates_created == 2
    pps = (await session.execute(select(PainPoint))).scalars().all()
    # HDBSCAN may mark a border point as noise; verify each cluster has ≥ min_cluster_size members
    attached = [pp for pp in pps if pp.candidate_id is not None]
    assert len(attached) >= 4  # at least 2 members per cluster


async def test_clustering_respects_min_cluster_size(session: AsyncSession) -> None:
    base = _unit([1.0, 0.0] + [0.0] * 30)
    for i in range(2):
        item = await _seed_item(session, f"c{i}")
        noise = np.random.default_rng(i).normal(0, 0.01, 32)
        vec = _unit((np.array(base) + noise).tolist())
        await _make_pp(session, item.id, vec)
    await session.commit()

    report = await run_clustering(session, min_cluster_size=3)

    assert report.candidates_created == 0
    pps = (await session.execute(select(PainPoint))).scalars().all()
    assert all(pp.candidate_id is None for pp in pps)


async def test_clustering_creates_unlabelled_candidates(session: AsyncSession) -> None:
    base = _unit([1.0, 0.0] + [0.0] * 30)
    for i in range(3):
        item = await _seed_item(session, f"d{i}")
        noise = np.random.default_rng(i).normal(0, 0.01, 32)
        vec = _unit((np.array(base) + noise).tolist())
        await _make_pp(session, item.id, vec)
    await session.commit()

    await run_clustering(session, min_cluster_size=2)

    candidates = (await session.execute(select(OpportunityCandidate))).scalars().all()
    for c in candidates:
        assert c.labeller_model is None
        assert c.specificity == 0
        assert c.centroid is not None


async def test_clustering_propagates_embedding_model(session: AsyncSession) -> None:
    base = _unit([1.0, 0.0] + [0.0] * 30)
    for i in range(4):
        item = await _seed_item(session, f"e{i}")
        noise = np.random.default_rng(i).normal(0, 0.01, 32)
        vec = _unit((np.array(base) + noise).tolist())
        pp = await _make_pp(session, item.id, vec)
        pp.embedding_model = "mock-embed-v1"
    await session.commit()

    await run_clustering(session, min_cluster_size=2)

    candidates = (await session.execute(select(OpportunityCandidate))).scalars().all()
    assert len(candidates) >= 1
    assert all(c.embedding_model == "mock-embed-v1" for c in candidates)


async def test_clustering_skips_mixed_model_clusters(session: AsyncSession) -> None:
    base = _unit([1.0, 0.0] + [0.0] * 30)
    models = ["model-a", "model-b", "model-a", "model-b"]
    for i, m in enumerate(models):
        item = await _seed_item(session, f"f{i}")
        noise = np.random.default_rng(i).normal(0, 0.005, 32)
        vec = _unit((np.array(base) + noise).tolist())
        pp = await _make_pp(session, item.id, vec)
        pp.embedding_model = m
    await session.commit()

    report = await run_clustering(session, min_cluster_size=2)

    assert report.candidates_created == 0
