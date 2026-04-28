"""Weekly data pruning: trims old SourceItem and non-aggregate NicheSignal rows."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select

from app.db import get_session
from app.models import MaintenanceState, NicheSignal, SourceItem

log = structlog.get_logger(__name__)

# metric_names produced by the daily aggregator — these rows are kept regardless of age.
_DAILY_AGGREGATE_METRIC_NAMES = frozenset({
    "mention_count",
    "github_stars_total",
    "hn_points_total",
    "reddit_ups_total",
    "appstore_install_proxy",
})


@dataclass
class PruneReport:
    source_items_deleted: int
    signals_deleted: int
    duration_ms: float
    ran_at: datetime


async def prune_old_data(
    now: datetime,
    *,
    source_retention_days: int = 90,
    signal_retention_days: int = 30,
) -> PruneReport:
    """Delete stale rows and update MaintenanceState.last_pruned_at.

    SourceItem rows older than source_retention_days are always pruned.
    NicheSignal rows older than signal_retention_days are pruned only when
    their metric_name is not in _DAILY_AGGREGATE_METRIC_NAMES (daily
    aggregates are kept so percentile rank history survives).
    """
    t0 = now.timestamp()
    source_cutoff = now - timedelta(days=source_retention_days)
    signal_cutoff = now - timedelta(days=signal_retention_days)

    async with get_session() as session:
        r1 = await session.execute(
            delete(SourceItem).where(SourceItem.created_at < source_cutoff)
        )
        source_deleted = r1.rowcount

        r2 = await session.execute(
            delete(NicheSignal).where(
                NicheSignal.metric_timestamp < signal_cutoff,
                NicheSignal.metric_name.not_in(_DAILY_AGGREGATE_METRIC_NAMES),
            )
        )
        signals_deleted = r2.rowcount

        state = (await session.execute(select(MaintenanceState))).scalar_one_or_none()
        if state is None:
            state = MaintenanceState(last_pruned_at=now)
            session.add(state)
        else:
            state.last_pruned_at = now

        await session.commit()

    duration_ms = (datetime.now(UTC).timestamp() - t0) * 1000
    report = PruneReport(
        source_items_deleted=source_deleted,
        signals_deleted=signals_deleted,
        duration_ms=round(duration_ms, 1),
        ran_at=now,
    )
    log.info(
        "pruning_complete",
        component="pruning",
        source_items_deleted=source_deleted,
        signals_deleted=signals_deleted,
        duration_ms=report.duration_ms,
    )
    return report
