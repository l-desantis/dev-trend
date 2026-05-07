"""Bulk backfill orchestrator for fresh installs.

Runs once on startup when the SourceItem table is empty:
  1. Fetches ~30 days of history from every connector (sequentially).
     GitHub and HN use weekly sub-windows so each window gets its own
     per-source item cap (~4× more items than a single 30-day query).
  2. Runs the v4 pipeline over the backfilled corpus.
"""
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog

from app.config import Settings
from app.ingestion.base import BaseConnector
from app.llm.base import LLMAdapter
from app.llm.embedding_base import EmbeddingAdapter

log = structlog.get_logger(__name__)

# Sources that benefit from windowed queries (have an upper-bound date filter).
_WINDOWED_SOURCES = {"github", "hn"}
_WINDOW_DAYS = 7


def _weekly_windows(since: datetime, until: datetime) -> list[tuple[datetime, datetime]]:
    """Split [since, until) into consecutive 7-day windows."""
    windows: list[tuple[datetime, datetime]] = []
    cursor = since
    while cursor < until:
        window_end = min(cursor + timedelta(days=_WINDOW_DAYS), until)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


@dataclass
class BackfillReport:
    history_days: int
    items_per_source: dict[str, int] = field(default_factory=dict)
    painpoints_created: int = 0
    candidates_created: int = 0
    labelled: int = 0
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "history_days": self.history_days,
            "items_per_source": self.items_per_source,
            "painpoints_created": self.painpoints_created,
            "candidates_created": self.candidates_created,
            "labelled": self.labelled,
            "duration_s": round(self.duration_s, 1),
        }


async def bulk_backfill(
    connectors: list[BaseConnector],
    llm: LLMAdapter,
    embedder: EmbeddingAdapter,
    settings: Settings,
    history_days: int = 30,
    extraction_limit: int | None = None,
) -> BackfillReport:
    """One-shot bulk backfill: fetch → v4 pipeline.

    Connectors are called sequentially to avoid rate-limit pile-ups.
    When history_days > 7, GitHub and HN are fetched in weekly sub-windows.
    Idempotent: re-running is safe due to (source_type, external_id) uniqueness.
    """
    start = time.monotonic()
    report = BackfillReport(history_days=history_days)
    now = datetime.now(UTC)
    since = now - timedelta(days=history_days)

    log.info("bulk_backfill_start", component="backfill", history_days=history_days)

    # 1. Sequential connector fetch
    windows = _weekly_windows(since, now) if history_days > _WINDOW_DAYS else [(since, now)]

    for connector in connectors:
        total_inserted = 0
        try:
            if connector.source_type in _WINDOWED_SOURCES and len(windows) > 1:
                for w_start, w_end in windows:
                    status = await connector.run(since=w_start, until=w_end)
                    total_inserted += status.items_ingested
                    log.info(
                        "backfill_window_done",
                        component="backfill",
                        source_type=connector.source_type,
                        window_start=w_start.date().isoformat(),
                        window_end=w_end.date().isoformat(),
                        items=status.items_ingested,
                    )
            else:
                status = await connector.run(since=since)
                total_inserted = status.items_ingested

            report.items_per_source[connector.source_type] = total_inserted
            log.info(
                "backfill_connector_done",
                component="backfill",
                source_type=connector.source_type,
                items_total=total_inserted,
            )
        except Exception as exc:
            log.error(
                "backfill_connector_error",
                component="backfill",
                source_type=connector.source_type,
                error=str(exc),
            )
            report.items_per_source[connector.source_type] = total_inserted

    # 2. Run v4 pipeline over backfilled corpus
    try:
        from app.db import _get_session_factory
        from app.pipeline.orchestrator import run_pipeline
        session_factory = _get_session_factory()
        pipeline_report = await run_pipeline(session_factory, llm, embedder, settings, since=since, extraction_limit=extraction_limit)
        if pipeline_report.extraction:
            report.painpoints_created = pipeline_report.extraction.painpoints_created
        if pipeline_report.clustering:
            report.candidates_created = pipeline_report.clustering.candidates_created
        if pipeline_report.labelling:
            report.labelled = pipeline_report.labelling.labelled
    except BaseException as exc:
        report.duration_s = time.monotonic() - start
        log.error(
            "backfill_pipeline_error",
            component="backfill",
            error=repr(exc),
            exc_type=type(exc).__name__,
            exc_info=True,
            **report.to_dict(),
        )
        raise

    report.duration_s = time.monotonic() - start
    log.info("bulk_backfill_complete", component="backfill", **report.to_dict())
    return report
