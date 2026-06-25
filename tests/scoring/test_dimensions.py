"""Tests for app/scoring/dimensions.py"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CandidateValidation,
    OpportunityCandidate,
    PainPoint,
    SourceItem,
)
from app.scoring.dimensions import (
    frequency_raw,
    momentum_raw,
    source_diversity_raw,
    specificity_raw,
    validation_curve,
)




async def _add_candidate(session: AsyncSession, **kwargs) -> OpportunityCandidate:
    c = OpportunityCandidate(problem_statement="test", specificity=3, **kwargs)
    session.add(c)
    await session.flush()
    return c


async def _add_source(session: AsyncSession, source_type: str = "reddit", external_id: str = "r1", **meta) -> SourceItem:
    si = SourceItem(
        source_type=source_type,
        external_id=external_id,
        metadata_json=meta or None,
    )
    session.add(si)
    await session.flush()
    return si


async def _add_pp(session: AsyncSession, candidate_id: int, source_item_id: int, days_ago: float = 0) -> PainPoint:
    pp = PainPoint(
        source_item_id=source_item_id,
        candidate_id=candidate_id,
        extractor_model="mock",
        extracted_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    session.add(pp)
    await session.flush()
    return pp


async def test_frequency_raw_counts_within_window(session: AsyncSession) -> None:
    c = await _add_candidate(session)
    si = await _add_source(session)
    # 3 within window, 2 outside
    for i in range(3):
        await _add_pp(session, c.id, si.id, days_ago=i * 5)
    for i in range(2):
        await _add_pp(session, c.id, si.id, days_ago=35 + i)
    await session.commit()

    result = await frequency_raw(session, c.id, window_days=30)
    assert result == 3.0


async def test_momentum_raw_positive_for_growing_attachment(session: AsyncSession) -> None:
    c = await _add_candidate(session)
    si = await _add_source(session, external_id="r2")
    now = datetime.now(UTC)
    # Rising counts: 1 on day-6, 2 on day-5, 3 on day-4, 4 on day-3, 5 on day-2
    for day, count in enumerate([1, 2, 3, 4, 5], start=2):
        for _ in range(count):
            pp = PainPoint(
                source_item_id=si.id,
                candidate_id=c.id,
                extractor_model="mock",
                extracted_at=now - timedelta(days=7 - day),
            )
            session.add(pp)
    await session.commit()

    slope = await momentum_raw(session, c.id, window_days=7)
    assert slope > 0


async def test_source_diversity_distinct_buckets(session: AsyncSession) -> None:
    c = await _add_candidate(session)
    si_reddit = await _add_source(session, source_type="reddit", external_id="r3", subreddit="startups")
    si_reddit2 = await _add_source(session, source_type="reddit", external_id="r4", subreddit="SideProject")
    si_hn = await _add_source(session, source_type="hn", external_id="hn1")
    for si in [si_reddit, si_reddit2, si_hn]:
        await _add_pp(session, c.id, si.id)
    await session.commit()

    result = await source_diversity_raw(session, c.id)
    assert result == 3.0


def test_validation_curve_each_band() -> None:
    assert validation_curve(0, 0) is None
    assert validation_curve(3, 2000) == 90.0
    assert validation_curve(10, 10000) == 70.0
    assert validation_curve(25, 50000) == 30.0


def test_validation_curve_returns_none_for_zero_repos() -> None:
    # repo_count == 0 is no-signal (usually a search miss, not verified novelty),
    # so the scorer drops the validation dimension rather than rewarding 30/100.
    assert validation_curve(0, 0) is None
    assert validation_curve(0, 9999) is None


def test_validation_curve_still_returns_score_for_nonzero() -> None:
    assert validation_curve(3, 1000) == 90.0
    assert validation_curve(10, 10_000) == 70.0
    assert validation_curve(100, 100_000) == 30.0


def test_specificity_raw_mapping() -> None:
    c = OpportunityCandidate(problem_statement="x", specificity=3)
    assert specificity_raw(c) == 60.0
