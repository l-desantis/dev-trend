"""Daily aggregation: SourceItem rows → NicheSignal rows per niche×source×metric."""
from datetime import datetime

import structlog
from sqlalchemy import delete, func, select

from app.db import get_session
from app.utils.datetime_utils import utc_day_bounds
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
