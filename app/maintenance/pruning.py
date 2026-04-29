"""Weekly data pruning: trims old SourceItem rows (PainPoints cascade-delete)."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select

from app.db import get_session
from app.models import MaintenanceState, SourceItem

log = structlog.get_logger(__name__)


@dataclass
class PruneReport:
    source_items_deleted: int
    duration_ms: float
    ran_at: datetime


async def prune_old_data(
    now: datetime,
    *,
    source_retention_days: int = 90,
    signal_retention_days: int = 30,  # kept for API compatibility
) -> PruneReport:
    """Delete stale SourceItem rows and update MaintenanceState.last_pruned_at.

    PainPoints are cascade-deleted via the FK ondelete='CASCADE' on source_item_id.
    NicheSignal pruning removed in v4 (no NicheSignal table).
    """
    t0 = now.timestamp()
    source_cutoff = now - timedelta(days=source_retention_days)

    async with get_session() as session:
        r1 = await session.execute(
            delete(SourceItem).where(SourceItem.created_at < source_cutoff)
        )
        source_deleted = r1.rowcount

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
        duration_ms=round(duration_ms, 1),
        ran_at=now,
    )
    log.info(
        "pruning_complete",
        component="pruning",
        source_items_deleted=source_deleted,
        duration_ms=report.duration_ms,
    )
    return report
