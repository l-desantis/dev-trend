"""Daily aggregation: SourceItem rows → NicheSignal rows per niche×source×metric."""
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, func, select

from app.db import get_session
from app.utils.datetime_utils import utc_day_bounds, utc_start_of_day
from app.models import NicheSignal, SourceItem

log = structlog.get_logger(__name__)

# Per-source metric name and the metadata key to sum.
_SOURCE_METRICS: dict[str, tuple[str, str]] = {
    "github": ("github_stars_total", "stars"),
    "hn": ("hn_points_total", "points"),
    "reddit": ("reddit_ups_total", "ups"),
    "appstore": ("appstore_install_proxy", "install_proxy"),
}


async def aggregate_daily_signals(as_of: datetime) -> int:
    """Write one NicheSignal row per (niche_id, source_type, metric_name) for as_of's UTC day.

    Returns the number of rows written. Idempotent: existing signals with the same
    metric_timestamp day for touched niches are removed before insert.
    """
    day_start, day_end = utc_day_bounds(as_of)

    async with get_session() as session:
        # mention_count per (niche_id, source_type)
        mention_stmt = (
            select(
                SourceItem.niche_id,
                SourceItem.source_type,
                func.count(SourceItem.id).label("n"),
            )
            .where(
                SourceItem.niche_id.is_not(None),
                SourceItem.ingested_at >= day_start,
                SourceItem.ingested_at < day_end,
            )
            .group_by(SourceItem.niche_id, SourceItem.source_type)
        )
        mention_rows = (await session.execute(mention_stmt)).all()

        # Per-source specific totals: fetch items and sum metadata in Python
        # (SQLite JSON_EXTRACT works but varies by version; keep it portable).
        items_stmt = (
            select(SourceItem)
            .where(
                SourceItem.niche_id.is_not(None),
                SourceItem.ingested_at >= day_start,
                SourceItem.ingested_at < day_end,
            )
        )
        items = (await session.execute(items_stmt)).scalars().all()

        source_totals: dict[tuple[int, str, str], float] = {}
        for item in items:
            metric = _SOURCE_METRICS.get(item.source_type)
            if metric is None:
                continue
            metric_name, meta_key = metric
            value = 0.0
            if item.metadata_json is not None:
                raw = item.metadata_json.get(meta_key)
                if isinstance(raw, (int, float)):
                    value = float(raw)
            assert item.niche_id is not None  # guaranteed by is_not(None) filter above
            key = (item.niche_id, item.source_type, metric_name)
            source_totals[key] = source_totals.get(key, 0.0) + value

        # Idempotency: delete any existing NicheSignal rows for this day
        # that we're about to (re)write.
        touched_niche_ids = {nid for nid, _, _ in mention_rows} | {
            nid for (nid, _, _) in source_totals.keys()
        }
        if touched_niche_ids:
            await session.execute(
                delete(NicheSignal).where(
                    NicheSignal.niche_id.in_(touched_niche_ids),
                    NicheSignal.metric_timestamp >= day_start,
                    NicheSignal.metric_timestamp < day_end,
                )
            )

        to_add: list[NicheSignal] = []
        for niche_id, source_type, n in mention_rows:
            to_add.append(NicheSignal(
                niche_id=niche_id,
                source_type=source_type,
                metric_name="mention_count",
                metric_value=float(n),
                metric_timestamp=day_start,
            ))
        for (niche_id, source_type, metric_name), total in source_totals.items():
            to_add.append(NicheSignal(
                niche_id=niche_id,
                source_type=source_type,
                metric_name=metric_name,
                metric_value=total,
                metric_timestamp=day_start,
            ))

        session.add_all(to_add)
        await session.commit()

    log.info(
        "Signal aggregation complete",
        component="signal_aggregator",
        day=day_start.isoformat(),
        rows=len(to_add),
    )
    return len(to_add)


async def rebuild_historical_signals(history_days: int) -> int:
    """Bin SourceItems by created_at into per-day NicheSignal rows for the backfill window.

    Reads SourceItems whose created_at falls within [now-history_days, now), groups them
    by (date, niche_id, source_type), and upserts NicheSignal rows with metric_timestamp
    set to the UTC start-of-day matching each item's original created_at.

    Returns the number of NicheSignal rows written.
    """
    now = datetime.now(UTC)
    window_start = utc_start_of_day(now) - timedelta(days=history_days)

    async with get_session() as session:
        items_stmt = (
            select(SourceItem)
            .where(
                SourceItem.niche_id.is_not(None),
                SourceItem.created_at.is_not(None),
                SourceItem.created_at >= window_start,
                SourceItem.created_at < now,
            )
        )
        items = (await session.execute(items_stmt)).scalars().all()

        # Group by (day_start, niche_id, source_type)
        mention_counts: dict[tuple[datetime, int, str], int] = {}
        source_totals: dict[tuple[datetime, int, str, str], float] = {}

        for item in items:
            if item.created_at is None or item.niche_id is None:
                continue
            created_at = item.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            day_start = utc_start_of_day(created_at)
            mkey = (day_start, item.niche_id, item.source_type)
            mention_counts[mkey] = mention_counts.get(mkey, 0) + 1

            metric = _SOURCE_METRICS.get(item.source_type)
            if metric is not None:
                metric_name, meta_key = metric
                value = 0.0
                if item.metadata_json is not None:
                    raw = item.metadata_json.get(meta_key)
                    if isinstance(raw, (int, float)):
                        value = float(raw)
                skey = (day_start, item.niche_id, item.source_type, metric_name)
                source_totals[skey] = source_totals.get(skey, 0.0) + value

        # Collect affected (niche_id, day) pairs for idempotent delete
        touched: dict[datetime, set[int]] = {}
        for day, niche_id, _ in mention_counts:
            touched.setdefault(day, set()).add(niche_id)
        for day, niche_id, _, _ in source_totals:
            touched.setdefault(day, set()).add(niche_id)

        for day, niche_ids in touched.items():
            day_end = day + timedelta(days=1)
            await session.execute(
                delete(NicheSignal).where(
                    NicheSignal.niche_id.in_(niche_ids),
                    NicheSignal.metric_timestamp >= day,
                    NicheSignal.metric_timestamp < day_end,
                )
            )

        to_add: list[NicheSignal] = []
        for (day, niche_id, source_type), count in mention_counts.items():
            to_add.append(NicheSignal(
                niche_id=niche_id,
                source_type=source_type,
                metric_name="mention_count",
                metric_value=float(count),
                metric_timestamp=day,
            ))
        for (day, niche_id, source_type, metric_name), total in source_totals.items():
            to_add.append(NicheSignal(
                niche_id=niche_id,
                source_type=source_type,
                metric_name=metric_name,
                metric_value=total,
                metric_timestamp=day,
            ))

        session.add_all(to_add)
        await session.commit()

    log.info(
        "Historical signal rebuild complete",
        component="signal_aggregator",
        history_days=history_days,
        rows=len(to_add),
    )
    return len(to_add)
