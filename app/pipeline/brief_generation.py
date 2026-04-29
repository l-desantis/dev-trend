"""Stage 9 — Brief generation for top-N candidates at digest time."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import LLMAdapter
from app.models import CandidateBrief, OpportunityCandidate, PainPoint, SourceItem

log = structlog.get_logger(__name__)

_BRIEF_MAX_EVIDENCE = 5


def _build_evidence_snapshot(
    pain_points: list[PainPoint],
    source_map: dict[int, SourceItem],
) -> list[dict[str, Any]]:
    evidence = []
    for pp in pain_points[:_BRIEF_MAX_EVIDENCE]:
        si = source_map.get(pp.source_item_id)
        evidence.append({
            "problem_text": pp.problem_text,
            "audience": pp.audience,
            "source_type": si.source_type if si else None,
            "source_url": si.url if si else None,
            "excerpt": (pp.problem_text or "")[:200],
            "extracted_at": pp.extracted_at.isoformat() if pp.extracted_at else None,
        })
    return evidence


async def generate_briefs_for(
    session: AsyncSession,
    llm: LLMAdapter,
    candidates: list[OpportunityCandidate],
    *,
    timeout_s: float = 90.0,
) -> list[CandidateBrief]:
    """Generate LLM briefs for given candidates. Idempotent within same day."""
    as_of = datetime.now(UTC)
    today_start = datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC)

    results: list[CandidateBrief] = []

    for candidate in candidates:
        # Idempotency: skip if brief already exists today
        existing = await session.execute(
            select(CandidateBrief)
            .where(CandidateBrief.candidate_id == candidate.id)
            .where(CandidateBrief.generated_at >= today_start)
            .limit(1)
        )
        if existing.scalars().first() is not None:
            log.debug("brief_already_exists_today", candidate_id=candidate.id)
            continue

        # Fetch recent pain points + their source items
        pp_result = await session.execute(
            select(PainPoint)
            .where(PainPoint.candidate_id == candidate.id)
            .order_by(PainPoint.extracted_at.desc())
            .limit(_BRIEF_MAX_EVIDENCE)
        )
        pain_points = pp_result.scalars().all()

        source_ids = list({pp.source_item_id for pp in pain_points})
        source_map: dict[int, SourceItem] = {}
        if source_ids:
            si_result = await session.execute(
                select(SourceItem).where(SourceItem.id.in_(source_ids))
            )
            source_map = {si.id: si for si in si_result.scalars().all()}

        evidence_json = _build_evidence_snapshot(list(pain_points), source_map)

        context = {
            "problem_statement": candidate.problem_statement,
            "audience": candidate.audience,
            "why_now": candidate.why_now,
            "evidence": evidence_json,
        }

        try:
            brief_text = await asyncio.wait_for(
                llm.generate_brief(context), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            log.warning("brief_generation_timeout", candidate_id=candidate.id)
            continue
        except Exception as exc:
            log.warning("brief_generation_error", candidate_id=candidate.id, error=str(exc))
            continue

        headline = (candidate.problem_statement or "")[:120]
        brief = CandidateBrief(
            candidate_id=candidate.id,
            headline=headline,
            summary=brief_text,
            generated_at=as_of,
            model_name=llm.model_name,
            evidence_json=evidence_json,
        )
        session.add(brief)
        await session.flush()
        results.append(brief)

    await session.commit()
    return results
