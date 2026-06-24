"""Tests for Stage 3 — identity resolution."""
import math

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import OpportunityCandidate, PainPoint, SourceItem
from app.pipeline.identity_resolution import run_identity_resolution


async def _seed_item(session: AsyncSession) -> SourceItem:
    item = SourceItem(source_type="reddit", external_id="t3_x", role="extraction")
    session.add(item)
    await session.flush()
    return item


async def test_identity_attaches_when_above_threshold(session: AsyncSession) -> None:
    item = await _seed_item(session)
    # Candidate centroid close to [1, 0, ...]
    centroid = [1.0] + [0.0] * 31
    candidate = OpportunityCandidate(
        problem_statement="test", specificity=1, centroid=centroid, is_archived=False
    )
    session.add(candidate)
    await session.flush()

    # PainPoint embedding very close to centroid
    embedding = [0.99] + [0.1] + [0.0] * 30
    pp = PainPoint(
        source_item_id=item.id, extractor_model="mock",
        problem_text="issue", audience="users", embedding=embedding
    )
    session.add(pp)
    await session.commit()

    report = await run_identity_resolution(session, threshold=0.82)

    assert report.attached == 1
    pp_refreshed = (await session.execute(select(PainPoint))).scalar_one()
    assert pp_refreshed.candidate_id == candidate.id


async def test_identity_leaves_unattached_below_threshold(session: AsyncSession) -> None:
    item = await _seed_item(session)
    centroid = [1.0] + [0.0] * 31
    candidate = OpportunityCandidate(
        problem_statement="test", specificity=1, centroid=centroid, is_archived=False
    )
    session.add(candidate)
    await session.flush()

    # Orthogonal embedding — should not match
    embedding = [0.0, 1.0] + [0.0] * 30
    pp = PainPoint(
        source_item_id=item.id, extractor_model="mock",
        problem_text="different", audience="users", embedding=embedding
    )
    session.add(pp)
    await session.commit()

    report = await run_identity_resolution(session, threshold=0.82)

    assert report.attached == 0
    pp_refreshed = (await session.execute(select(PainPoint))).scalar_one()
    assert pp_refreshed.candidate_id is None


async def test_identity_ignores_archived_candidates(session: AsyncSession) -> None:
    item = await _seed_item(session)
    centroid = [1.0] + [0.0] * 31
    candidate = OpportunityCandidate(
        problem_statement="archived", specificity=1, centroid=centroid, is_archived=True
    )
    session.add(candidate)
    await session.flush()

    embedding = [0.99] + [0.1] + [0.0] * 30
    pp = PainPoint(
        source_item_id=item.id, extractor_model="mock",
        problem_text="p", audience="a", embedding=embedding
    )
    session.add(pp)
    await session.commit()

    report = await run_identity_resolution(session, threshold=0.5)

    assert report.attached == 0


async def test_identity_no_candidates_yet(session: AsyncSession) -> None:
    item = await _seed_item(session)
    pp = PainPoint(
        source_item_id=item.id, extractor_model="mock",
        problem_text="p", audience="a", embedding=[1.0] + [0.0] * 31
    )
    session.add(pp)
    await session.commit()

    report = await run_identity_resolution(session, threshold=0.82)

    assert report.attached == 0
    assert report.unattached_checked == 0


async def test_identity_attaches_in_recalibrated_band(session: AsyncSession) -> None:
    """A ~0.70-cosine pain point attaches at 0.65 (new) but not at 0.82 (old)."""
    item = await _seed_item(session)
    centroid = [1.0] + [0.0] * 31
    candidate = OpportunityCandidate(
        problem_statement="test", specificity=1, centroid=centroid, is_archived=False
    )
    session.add(candidate)
    await session.flush()

    # Unit vector at cosine 0.70 to the centroid: [0.70, sqrt(1-0.49), 0...]
    embedding = [0.70, math.sqrt(1.0 - 0.49)] + [0.0] * 30
    pp = PainPoint(
        source_item_id=item.id, extractor_model="mock",
        problem_text="moderately similar", audience="users", embedding=embedding,
    )
    session.add(pp)
    await session.commit()

    # Old threshold rejects it
    report_old = await run_identity_resolution(session, threshold=0.82)
    assert report_old.attached == 0

    # Recalibrated threshold attaches it
    report_new = await run_identity_resolution(session, threshold=0.65)
    assert report_new.attached == 1
    pp_refreshed = (await session.execute(select(PainPoint))).scalar_one()
    assert pp_refreshed.candidate_id == candidate.id


async def test_centroid_recomputation_correct(session: AsyncSession) -> None:
    item = await _seed_item(session)
    centroid = [1.0] + [0.0] * 31
    candidate = OpportunityCandidate(
        problem_statement="test", specificity=1, centroid=centroid, is_archived=False
    )
    session.add(candidate)
    await session.flush()

    embedding = [0.99] + [0.1] + [0.0] * 30
    pp = PainPoint(
        source_item_id=item.id, extractor_model="mock",
        problem_text="p", audience="a", embedding=embedding
    )
    session.add(pp)
    await session.commit()

    await run_identity_resolution(session, threshold=0.5)

    cand_refreshed = (await session.execute(select(OpportunityCandidate))).scalar_one()
    assert cand_refreshed.centroid is not None
    # centroid should be unit-normalised
    import numpy as np
    norm = np.linalg.norm(cand_refreshed.centroid)
    assert abs(norm - 1.0) < 1e-5
