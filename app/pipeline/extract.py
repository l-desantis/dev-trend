"""Stage 1 — extract pain points from SourceItems."""
import time
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import LLMAdapter
from app.models import PainPoint, SourceItem

log = structlog.get_logger(__name__)


@dataclass
class ExtractionReport:
    processed: int = 0
    painpoints_created: int = 0
    no_signal: int = 0
    failed: int = 0
    duration_ms: int = 0


async def run_extraction(
    session: AsyncSession,
    llm: LLMAdapter,
    *,
    since: datetime | None = None,
    force: bool = False,
    batch_size: int = 20,
) -> ExtractionReport:
    """Stage 1: extract pain points from pending SourceItems with role='extraction'."""
    start = time.monotonic()
    report = ExtractionReport()

    query = (
        select(SourceItem)
        .where(SourceItem.role == "extraction")
        .where(SourceItem.extraction_state == "pending")
    )
    if since is not None:
        query = query.where(SourceItem.ingested_at >= since)

    rows = (await session.execute(query)).scalars().all()

    for item in rows:
        # Idempotency: skip if already extracted with this model (unless force)
        if not force:
            existing = (
                await session.execute(
                    select(PainPoint)
                    .where(PainPoint.source_item_id == item.id)
                    .where(PainPoint.extractor_model == llm.model_name)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue

        report.processed += 1
        text = f"{item.title or ''}\n{item.body or ''}"[:4000]

        try:
            draft = await llm.extract_pain_point(text)
        except Exception as exc:
            log.warning("extract_failed", source_item_id=item.id, error=str(exc))
            item.extraction_state = "failed"
            report.failed += 1
            continue

        if not draft.has_unmet_need:
            item.extraction_state = "no_signal"
            report.no_signal += 1
        else:
            pp = PainPoint(
                source_item_id=item.id,
                extractor_model=llm.model_name,
                problem_text=draft.problem_text,
                audience=draft.audience,
                urgency_cue=draft.urgency_cue,
                current_workaround=draft.current_workaround,
            )
            session.add(pp)
            item.extraction_state = "extracted"
            report.painpoints_created += 1

    await session.commit()
    report.duration_ms = int((time.monotonic() - start) * 1000)
    log.info(
        "extraction_complete",
        **{k: v for k, v in report.__dict__.items()},
    )
    return report
