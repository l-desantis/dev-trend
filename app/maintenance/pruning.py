"""Weekly data pruning: trims old SourceItem, CandidateValidation, and LifecycleEvent rows."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select, text

from app.db import get_session
from app.models import CandidateValidation, LifecycleEvent, MaintenanceState, SourceItem

log = structlog.get_logger(__name__)


@dataclass
class PruneReport:
    source_items_deleted: int
    candidate_validations_deleted: int
    lifecycle_events_deleted: int
    duration_ms: float
    ran_at: datetime


async def prune_old_data(
    now: datetime,
    *,
    source_retention_days: int = 90,
    signal_retention_days: int = 30,
) -> PruneReport:
    """Delete stale rows and update MaintenanceState.last_pruned_at.

    - SourceItem rows older than source_retention_days are deleted.
      PainPoints cascade-delete via FK ondelete='CASCADE'.
    - CandidateValidation: keep only the most-recent snapshot per candidate;
      delete older rows beyond signal_retention_days.
    - LifecycleEvent rows older than signal_retention_days are deleted.
    """
    t0 = now.timestamp()
    source_cutoff = now - timedelta(days=source_retention_days)
    signal_cutoff = now - timedelta(days=signal_retention_days)

    async with get_session() as session:
        # Delete old SourceItem rows (PainPoints cascade automatically)
        r1 = await session.execute(
            delete(SourceItem).where(SourceItem.created_at < source_cutoff)
        )
        source_deleted = r1.rowcount

        # Delete old CandidateValidation rows (keep newest per candidate + rows within window)
        r2 = await session.execute(
            text("""
                DELETE FROM candidate_validations
                 WHERE validated_at < :cutoff
                   AND id NOT IN (
                       SELECT MAX(id) FROM candidate_validations GROUP BY candidate_id
                   )
            """),
            {"cutoff": signal_cutoff},
        )
        cv_deleted = r2.rowcount

        # Delete old LifecycleEvent rows
        r3 = await session.execute(
            delete(LifecycleEvent).where(LifecycleEvent.recorded_at < signal_cutoff)
        )
        le_deleted = r3.rowcount

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
        candidate_validations_deleted=cv_deleted,
        lifecycle_events_deleted=le_deleted,
        duration_ms=round(duration_ms, 1),
        ran_at=now,
    )
    log.info(
        "pruning_complete",
        component="pruning",
        source_items_deleted=source_deleted,
        candidate_validations_deleted=cv_deleted,
        lifecycle_events_deleted=le_deleted,
        duration_ms=report.duration_ms,
    )
    return report
