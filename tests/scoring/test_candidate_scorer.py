"""Tests for app/scoring/candidate_scorer.py"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CandidateScoreHistory, OpportunityCandidate, PainPoint, SourceItem
from app.scoring.candidate_scorer import WEIGHTS, score_all_candidates


async def _seed_candidate_with_pain_points(
    session: AsyncSession,
    *,
    n: int = 3,
    specificity: int = 3,
) -> OpportunityCandidate:
    c = OpportunityCandidate(problem_statement="test", specificity=specificity)
    session.add(c)
    await session.flush()
    si = SourceItem(source_type="reddit", external_id=f"r_{c.id}")
    session.add(si)
    await session.flush()
    for i in range(n):
        pp = PainPoint(
            source_item_id=si.id,
            candidate_id=c.id,
            extractor_model="mock",
            extracted_at=datetime.now(UTC) - timedelta(days=i),
        )
        session.add(pp)
    await session.commit()
    return c


async def test_score_all_candidates_writes_history(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    for _ in range(3):
        await _seed_candidate_with_pain_points(session)

    as_of = datetime.now(UTC)
    rows = await score_all_candidates(session, as_of=as_of)
    assert len(rows) == 3


async def test_score_idempotent_same_day(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    await _seed_candidate_with_pain_points(session)
    await _seed_candidate_with_pain_points(session)
    await _seed_candidate_with_pain_points(session)

    as_of = datetime.now(UTC)
    await score_all_candidates(session, as_of=as_of)
    rows2 = await score_all_candidates(session, as_of=as_of)

    from sqlalchemy import select, func
    result = await session.execute(select(func.count(CandidateScoreHistory.id)))
    total = result.scalar_one()
    assert total == 3  # not 6


async def test_score_skips_below_specificity_gate(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    await _seed_candidate_with_pain_points(session, specificity=2)

    as_of = datetime.now(UTC)
    rows = await score_all_candidates(session, as_of=as_of)
    assert rows == []


async def test_score_breakdown_includes_raw_and_normalised(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    await _seed_candidate_with_pain_points(session)

    rows = await score_all_candidates(session, as_of=datetime.now(UTC))
    assert len(rows) == 1
    bd = rows[0].score_breakdown_json
    assert "raw" in bd["frequency"]
    assert "score" in bd["frequency"]
    assert "raw" in bd["momentum"]
    assert isinstance(bd["validation"], float)


async def test_score_total_matches_weighted_sum(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    await _seed_candidate_with_pain_points(session)

    rows = await score_all_candidates(session, as_of=datetime.now(UTC))
    assert len(rows) == 1
    bd = rows[0].score_breakdown_json
    expected = (
        bd["frequency"]["score"] * WEIGHTS["frequency"]
        + bd["momentum"]["score"] * WEIGHTS["momentum"]
        + bd["source_diversity"]["score"] * WEIGHTS["source_diversity"]
        + bd["validation"] * WEIGHTS["validation"]
        + bd["specificity"] * WEIGHTS["specificity"]
    )
    assert abs(rows[0].score_total - expected) < 0.001
