"""Raw dimension computations for opportunity scoring.

All functions return raw values before percentile normalisation.
validation_curve is the exception: it returns 0–100 directly per spec §5.2.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.trend_features import rolling_slope
from app.models import CandidateValidation, OpportunityCandidate, PainPoint, SourceItem

if TYPE_CHECKING:
    pass


def _source_bucket(source_item: SourceItem) -> str:
    meta = source_item.metadata_json or {}
    if source_item.source_type == "reddit":
        sub = meta.get("subreddit") or meta.get("sub")
        if sub:
            return f"reddit:{sub}"
    if source_item.source_type == "playstore":
        app_id = meta.get("app_id")
        if app_id:
            return f"playstore:{app_id}"
    return source_item.source_type


async def frequency_raw(
    session: AsyncSession,
    candidate_id: int,
    *,
    window_days: int = 30,
) -> float:
    since = datetime.now(UTC) - timedelta(days=window_days)
    result = await session.execute(
        select(func.count(PainPoint.id))
        .where(PainPoint.candidate_id == candidate_id)
        .where(PainPoint.extracted_at >= since)
    )
    return float(result.scalar_one())


async def momentum_raw(
    session: AsyncSession,
    candidate_id: int,
    *,
    window_days: int = 7,
) -> float:
    since = datetime.now(UTC) - timedelta(days=window_days)
    result = await session.execute(
        select(
            func.date(PainPoint.extracted_at).label("day"),
            func.count(PainPoint.id).label("cnt"),
        )
        .where(PainPoint.candidate_id == candidate_id)
        .where(PainPoint.extracted_at >= since)
        .group_by(func.date(PainPoint.extracted_at))
        .order_by(func.date(PainPoint.extracted_at))
    )
    rows = result.all()
    if not rows:
        return 0.0
    counts = [float(r.cnt) for r in rows]
    return rolling_slope(counts)


async def source_diversity_raw(
    session: AsyncSession,
    candidate_id: int,
    *,
    window_days: int = 30,
) -> float:
    since = datetime.now(UTC) - timedelta(days=window_days)
    result = await session.execute(
        select(PainPoint, SourceItem)
        .join(SourceItem, PainPoint.source_item_id == SourceItem.id)
        .where(PainPoint.candidate_id == candidate_id)
        .where(PainPoint.extracted_at >= since)
    )
    rows = result.all()
    buckets = {_source_bucket(row.SourceItem) for row in rows}
    return float(len(buckets))


def validation_curve(repo_count: int, max_stars: int) -> float | None:
    """Non-monotonic validation score per spec §5.2. Returns 0–100 directly.

    Returns ``None`` when ``repo_count == 0``: an empty GitHub result is treated
    as no signal (likely a search miss, not a verified novel idea) and the
    candidate_scorer renormalizes the remaining dimensions in response.
    """
    if repo_count == 0:
        return None
    if repo_count <= 5 and max_stars <= 5_000:
        return 90.0
    if repo_count <= 20 and max_stars <= 20_000:
        return 70.0
    return 30.0


def specificity_raw(candidate: OpportunityCandidate) -> float:
    """Map specificity 1–5 to 20–100 linearly."""
    return float(candidate.specificity * 20)


async def get_latest_validation(
    session: AsyncSession,
    candidate_id: int,
) -> CandidateValidation | None:
    result = await session.execute(
        select(CandidateValidation)
        .where(CandidateValidation.candidate_id == candidate_id)
        .where(CandidateValidation.signal_type == "composite")
        .order_by(CandidateValidation.validated_at.desc())
        .limit(1)
    )
    return result.scalars().first()
