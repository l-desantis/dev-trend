"""Bulk backfill orchestrator for fresh installs.

Runs once on startup when the SourceItem table is empty:
  1. Fetches ~30 days of history from every connector (sequentially).
  2. Rebuilds per-day NicheSignal rows from each item's original created_at.
  3. Scores all niches day-by-day, oldest first, populating NicheScoreHistory.
  4. Generates OpportunityBriefs for each niche.
"""
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from app.agents.graph import run_brief_for_niche
from app.db import get_session
from app.features.signal_aggregator import rebuild_historical_signals
from app.forecasting.scoring import score_all_niches
from app.ingestion.base import BaseConnector
from app.llm.base import LLMAdapter
from app.models import Niche
from app.utils.datetime_utils import utc_start_of_day

log = structlog.get_logger(__name__)


@dataclass
class BackfillReport:
    history_days: int
    items_per_source: dict[str, int] = field(default_factory=dict)
    signal_rows: int = 0
    scores_written: int = 0
    briefs_generated: int = 0
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "history_days": self.history_days,
            "items_per_source": self.items_per_source,
            "signal_rows": self.signal_rows,
            "scores_written": self.scores_written,
            "briefs_generated": self.briefs_generated,
            "duration_s": round(self.duration_s, 1),
        }


async def bulk_backfill(
    connectors: list[BaseConnector],
    adapter: LLMAdapter,
    history_days: int = 30,
) -> BackfillReport:
    """One-shot bulk backfill: fetch → signal rebuild → scoring → briefs.

    Connectors are called sequentially to avoid rate-limit pile-ups.
    Idempotent: re-running is safe due to (source_type, external_id) uniqueness.
    """
    start = time.monotonic()
    report = BackfillReport(history_days=history_days)
    since = datetime.now(UTC) - timedelta(days=history_days)

    log.info("bulk_backfill_start", component="backfill", history_days=history_days)

    # 1. Sequential connector fetch
    for connector in connectors:
        try:
            status = await connector.run(since=since)
            report.items_per_source[connector.source_type] = status.items_ingested
            log.info(
                "backfill_connector_done",
                component="backfill",
                source_type=connector.source_type,
                items=status.items_ingested,
            )
        except Exception as exc:
            log.error(
                "backfill_connector_error",
                component="backfill",
                source_type=connector.source_type,
                error=str(exc),
            )
            report.items_per_source[connector.source_type] = 0

    # 2. Rebuild historical NicheSignal rows from created_at
    try:
        report.signal_rows = await rebuild_historical_signals(history_days)
    except Exception as exc:
        log.error("backfill_signal_rebuild_error", component="backfill", error=str(exc))

    # 3. Score all niches day-by-day, oldest first
    now = datetime.now(UTC)
    window_start = utc_start_of_day(now) - timedelta(days=history_days)
    for day_offset in range(history_days + 1):
        day = window_start + timedelta(days=day_offset)
        try:
            scored = await score_all_niches(day)
            report.scores_written += scored
        except Exception as exc:
            log.error(
                "backfill_scoring_error",
                component="backfill",
                day=day.isoformat(),
                error=str(exc),
            )

    # 4. Generate briefs for each niche
    try:
        async with get_session() as session:
            niche_ids = (await session.execute(select(Niche.id))).scalars().all()
        for nid in niche_ids:
            try:
                brief_id = await run_brief_for_niche(nid, adapter, triggered_by="backfill")
                if brief_id is not None:
                    report.briefs_generated += 1
            except Exception as exc:
                log.error(
                    "backfill_brief_error",
                    component="backfill",
                    niche_id=nid,
                    error=str(exc),
                )
    except Exception as exc:
        log.error("backfill_brief_job_error", component="backfill", error=str(exc))

    report.duration_s = time.monotonic() - start
    log.info("bulk_backfill_complete", component="backfill", **report.to_dict())
    return report
