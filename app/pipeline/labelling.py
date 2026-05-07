"""Stage 5 — label unlabelled OpportunityCandidates."""
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import LLMAdapter
from app.models import Category, OpportunityCandidate, PainPoint

log = structlog.get_logger(__name__)


@dataclass
class LabellingReport:
    unlabelled_found: int = 0
    labelled: int = 0
    failed: int = 0
    duration_ms: int = 0


async def run_labelling(
    session: AsyncSession,
    llm: LLMAdapter,
) -> LabellingReport:
    """Stage 5: label all candidates where labeller_model IS NULL."""
    start = time.monotonic()
    report = LabellingReport()

    unlabelled = (
        await session.execute(
            select(OpportunityCandidate).where(OpportunityCandidate.labeller_model.is_(None))
        )
    ).scalars().all()

    report.unlabelled_found = len(unlabelled)
    log.info("labelling_start", unlabelled_found=report.unlabelled_found)

    # Load all category slugs once
    all_slugs = list(
        (await session.execute(select(Category.slug))).scalars().all()
    )

    for candidate in unlabelled:
        # Get top 10 most-recent PainPoints as evidence
        pps = (
            await session.execute(
                select(PainPoint)
                .where(PainPoint.candidate_id == candidate.id)
                .order_by(PainPoint.extracted_at.desc())
                .limit(10)
            )
        ).scalars().all()

        evidence_texts = [
            f"- {pp.problem_text} [{pp.audience}]"
            for pp in pps
            if pp.problem_text
        ]

        try:
            label = await llm.label_cluster(evidence_texts, all_slugs)
        except Exception as exc:
            log.warning(
                "labelling_failed",
                candidate_id=candidate.id,
                error=str(exc),
            )
            report.failed += 1
            continue

        # Persist label fields (set labeller_model last — it's the "done" sentinel)
        candidate.problem_statement = label.problem_statement
        candidate.audience = label.audience
        candidate.why_now = label.why_now
        candidate.specificity = label.specificity

        # Category assignment
        category_id: int | None = None
        if label.suggested_category_slug:
            cat = (
                await session.execute(
                    select(Category).where(Category.slug == label.suggested_category_slug)
                )
            ).scalar_one_or_none()
            if cat is not None:
                category_id = cat.id

        candidate.category_id = category_id

        # Must be set last so any earlier failure leaves labeller_model=NULL
        candidate.labeller_model = llm.model_name
        candidate.last_labelled_at = datetime.now(UTC)
        await session.flush()

        # Propagate category to parent SourceItems
        if category_id is not None:
            await session.execute(
                text(
                    "UPDATE source_items SET category_id = :cat_id "
                    "WHERE id IN ("
                    "  SELECT source_item_id FROM pain_points WHERE candidate_id = :cid"
                    ")"
                ),
                {"cat_id": category_id, "cid": candidate.id},
            )

        report.labelled += 1
        log.debug("labelling_progress", processed=report.labelled + report.failed)

    await session.commit()
    report.duration_ms = int((time.monotonic() - start) * 1000)
    log.info("labelling_complete", **{k: v for k, v in report.__dict__.items()})
    return report
