"""Daily composite scorer.

Reads NicheSignal (daily aggregates) and NicheScoreHistory (rolling history),
computes Growth / Demand / Novelty raws, percentile-normalises each against
the niche's own 30-day history, and persists a NicheScoreHistory row.
"""
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.db import get_session
from app.features.trend_features import percentile_rank, rolling_slope
from app.models import Niche, NicheScoreHistory, NicheSignal, SourceItem

log = structlog.get_logger(__name__)

_DIMENSIONS = ("growth", "demand", "novelty")


def _day_start(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def _mention_sum_for_day(session, niche_id: int, day_start: datetime) -> float:
    stmt = (
        select(func.coalesce(func.sum(NicheSignal.metric_value), 0.0))
        .where(
            NicheSignal.niche_id == niche_id,
            NicheSignal.metric_name == "mention_count",
            NicheSignal.metric_timestamp == day_start,
        )
    )
    return float((await session.execute(stmt)).scalar_one())


async def _source_metric_for_day(
    session, niche_id: int, metric_name: str, day_start: datetime
) -> float:
    stmt = (
        select(func.coalesce(func.sum(NicheSignal.metric_value), 0.0))
        .where(
            NicheSignal.niche_id == niche_id,
            NicheSignal.metric_name == metric_name,
            NicheSignal.metric_timestamp == day_start,
        )
    )
    return float((await session.execute(stmt)).scalar_one())


async def _compute_growth_raw(session, niche_id: int, as_of: datetime, window_days: int) -> float:
    today = _day_start(as_of)
    daily_sums: list[float] = []
    for offset in range(window_days - 1, -1, -1):  # oldest → newest
        day = today - timedelta(days=offset)
        daily_sums.append(await _mention_sum_for_day(session, niche_id, day))
    return rolling_slope(daily_sums)


async def _compute_demand_raw(session, niche_id: int, as_of: datetime) -> float:
    today = _day_start(as_of)
    seven_ago = today - timedelta(days=7)
    mentions_today = await _mention_sum_for_day(session, niche_id, today)
    stars_today = await _source_metric_for_day(session, niche_id, "github_stars_total", today)
    stars_past = await _source_metric_for_day(session, niche_id, "github_stars_total", seven_ago)
    star_delta = max(0.0, stars_today - stars_past)
    install_proxy = await _source_metric_for_day(session, niche_id, "appstore_install_proxy", today)
    return mentions_today + star_delta + install_proxy


async def _compute_novelty_raw(session, niche_id: int, as_of: datetime, max_age_days: int) -> float:
    # Prefer created_at; fall back to ingested_at when the source provided none.
    stmt = (
        select(func.max(func.coalesce(SourceItem.created_at, SourceItem.ingested_at)))
        .where(SourceItem.niche_id == niche_id)
    )
    latest = (await session.execute(stmt)).scalar_one()
    if latest is None:
        return 0.0
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    as_of_utc = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    age_days = (as_of_utc - latest).total_seconds() / 86400.0
    return max(0.0, 1.0 - age_days / max_age_days)


async def _raw_history(
    session, niche_id: int, dimension: str, as_of: datetime, window_days: int
) -> list[float]:
    window_start = _day_start(as_of) - timedelta(days=window_days)
    stmt = (
        select(NicheScoreHistory.score_breakdown_json)
        .where(
            NicheScoreHistory.niche_id == niche_id,
            NicheScoreHistory.scored_at >= window_start,
            NicheScoreHistory.scored_at < _day_start(as_of),
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    values: list[float] = []
    for bd in rows:
        if not bd:
            continue
        dim = bd.get(dimension)
        if dim is None:
            continue
        raw = dim.get("raw")
        if isinstance(raw, (int, float)):
            values.append(float(raw))
    return values


async def score_niche(niche_id: int, as_of: datetime) -> NicheScoreHistory:
    """Compute today's score for a niche and persist to NicheScoreHistory.

    Delete-then-insert on (niche_id, scored_at = midnight UTC of as_of).
    """
    settings = get_settings()
    today = _day_start(as_of)

    async with get_session() as session:
        raws = {
            "growth": await _compute_growth_raw(
                session, niche_id, as_of, settings.scoring_growth_window_days
            ),
            "demand": await _compute_demand_raw(session, niche_id, as_of),
            "novelty": await _compute_novelty_raw(
                session, niche_id, as_of, settings.scoring_novelty_max_age_days
            ),
        }

        breakdown: dict[str, dict[str, float]] = {}
        for dim in _DIMENSIONS:
            history = await _raw_history(
                session, niche_id, dim, as_of, settings.scoring_normalization_window_days
            )
            normalized = percentile_rank(history, raws[dim])
            breakdown[dim] = {"raw": raws[dim], "normalized": normalized}

        total = (
            breakdown["growth"]["normalized"] * settings.growth_weight
            + breakdown["demand"]["normalized"] * settings.demand_weight
            + breakdown["novelty"]["normalized"] * settings.novelty_weight
        )

        await session.execute(
            delete(NicheScoreHistory).where(
                NicheScoreHistory.niche_id == niche_id,
                NicheScoreHistory.scored_at == today,
            )
        )
        row = NicheScoreHistory(
            niche_id=niche_id,
            score_total=total,
            score_breakdown_json=breakdown,
            scored_at=today,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    log.info(
        "Niche scored",
        component="scoring",
        niche_id=niche_id,
        score_total=round(total, 2),
        scored_at=today.isoformat(),
    )
    return row


async def score_all_niches(as_of: datetime) -> int:
    """Score every niche in the DB for as_of's UTC day. Returns niches scored."""
    async with get_session() as session:
        niche_ids = (await session.execute(select(Niche.id))).scalars().all()

    for nid in niche_ids:
        try:
            await score_niche(nid, as_of)
        except Exception as exc:
            log.error(
                "Niche scoring failed",
                component="scoring",
                niche_id=nid,
                error=str(exc),
            )
    return len(niche_ids)
