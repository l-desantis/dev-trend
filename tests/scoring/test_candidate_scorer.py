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
    # Seeded candidate has no CandidateValidation row → repo_count=0 → no-signal.
    assert bd["validation"] is None


async def test_score_total_matches_weighted_sum(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    await _seed_candidate_with_pain_points(session)

    rows = await score_all_candidates(session, as_of=datetime.now(UTC))
    assert len(rows) == 1
    bd = rows[0].score_breakdown_json
    base = (
        bd["frequency"]["score"] * WEIGHTS["frequency"]
        + bd["momentum"]["score"] * WEIGHTS["momentum"]
        + bd["source_diversity"]["score"] * WEIGHTS["source_diversity"]
        + bd["specificity"] * WEIGHTS["specificity"]
    )
    if bd["validation"] is None:
        # No-signal: validation dropped, remaining weights renormalized to 1.0.
        expected = base / (1.0 - WEIGHTS["validation"])
    else:
        expected = base + bd["validation"] * WEIGHTS["validation"]
    assert abs(rows[0].score_total - expected) < 0.001


async def test_score_renormalizes_when_validation_is_none(
    session: AsyncSession, monkeypatch
) -> None:
    """repo_count=0 → validation dropped, remaining four weights rescaled to 1.0."""
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from unittest.mock import AsyncMock

    import pytest

    from app.config import get_settings
    import app.scoring.candidate_scorer as cs

    get_settings.cache_clear()

    # Single candidate with no CandidateValidation row → repo_count=0.
    c = OpportunityCandidate(problem_statement="x", audience="y", specificity=5)
    session.add(c)
    await session.commit()

    # Force every raw input and the percentile normaliser to known values so the
    # expected total is computable from first principles.
    monkeypatch.setattr(cs, "frequency_raw", AsyncMock(return_value=10.0))
    monkeypatch.setattr(cs, "momentum_raw", AsyncMock(return_value=5.0))
    monkeypatch.setattr(cs, "source_diversity_raw", AsyncMock(return_value=3.0))
    monkeypatch.setattr(
        cs, "normalize_with_neutral_fallback", lambda raws: {k: 50.0 for k in raws}
    )

    rows = await score_all_candidates(session, as_of=datetime.now(UTC))

    assert len(rows) == 1
    breakdown = rows[0].score_breakdown_json
    assert breakdown["validation"] is None

    spec_raw = 100.0  # specificity 5 * 20
    weighted_no_val = (
        50.0 * WEIGHTS["frequency"]
        + 50.0 * WEIGHTS["momentum"]
        + 50.0 * WEIGHTS["source_diversity"]
        + spec_raw * WEIGHTS["specificity"]
    )
    expected = weighted_no_val / (1.0 - WEIGHTS["validation"])  # = 56.25
    assert rows[0].score_total == pytest.approx(expected, abs=1e-6)


async def test_score_uses_validation_when_present(
    session: AsyncSession, monkeypatch
) -> None:
    """Sanity check the non-renormalized path stays at the original total."""
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from unittest.mock import AsyncMock

    import pytest

    from app.config import get_settings
    from app.models import CandidateValidation
    import app.scoring.candidate_scorer as cs

    get_settings.cache_clear()

    c = OpportunityCandidate(problem_statement="x", audience="y", specificity=5)
    session.add(c)
    await session.flush()
    session.add(
        CandidateValidation(
            candidate_id=c.id,
            signal_type="composite",
            signal_value=3.0,
            validated_at=datetime.now(UTC),
            metadata_json={"repo_count": 3, "max_stars": 1000},  # → val_score 90
        )
    )
    await session.commit()

    monkeypatch.setattr(cs, "frequency_raw", AsyncMock(return_value=10.0))
    monkeypatch.setattr(cs, "momentum_raw", AsyncMock(return_value=5.0))
    monkeypatch.setattr(cs, "source_diversity_raw", AsyncMock(return_value=3.0))
    monkeypatch.setattr(
        cs, "normalize_with_neutral_fallback", lambda raws: {k: 50.0 for k in raws}
    )

    rows = await score_all_candidates(session, as_of=datetime.now(UTC))
    assert rows[0].score_breakdown_json["validation"] == 90.0
    expected = (
        50.0 * WEIGHTS["frequency"]
        + 50.0 * WEIGHTS["momentum"]
        + 50.0 * WEIGHTS["source_diversity"]
        + 90.0 * WEIGHTS["validation"]
        + 100.0 * WEIGHTS["specificity"]
    )  # = 63.0
    assert rows[0].score_total == pytest.approx(expected, abs=1e-6)
