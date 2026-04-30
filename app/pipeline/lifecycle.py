"""Stage 8 — Lifecycle state derivation and transition detection."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CandidateScoreHistory, LifecycleEvent, OpportunityCandidate

log = structlog.get_logger(__name__)


class LifecycleTransition(BaseModel):
    candidate_id: int
    old_state: str | None
    new_state: str
    score_total: float
    problem_statement: str
    lifecycle_event_id: int | None = None


def derive_lifecycle_state(
    candidate: OpportunityCandidate,
    history: list[CandidateScoreHistory],
) -> str | None:
    """Returns one of: 'emerging', 'hot', 'saturated', 'dormant', None.

    Uses normalised scores from the latest history row per spec §5.3.
    """
    if not history:
        return None

    latest = history[-1]
    bd = latest.score_breakdown_json or {}
    momentum = (bd.get("momentum") or {}).get("score", 0.0)
    frequency = (bd.get("frequency") or {}).get("score", 0.0)

    age_days = (latest.scored_at - candidate.created_at).days

    last_pp_age = (
        (latest.scored_at - candidate.last_evidence_at).days
        if candidate.last_evidence_at is not None
        else 0
    )

    if last_pp_age >= 14:
        return "dormant"
    if momentum >= 60 and frequency < 30 and age_days < 14:
        return "emerging"
    if momentum >= 60 and frequency >= 30:
        return "hot"
    if frequency >= 70 and momentum < 30:
        return "saturated"
    return None


async def update_lifecycle_states_and_emit_transitions(
    session: AsyncSession,
    *,
    as_of: datetime,
) -> list[LifecycleTransition]:
    """Derive new lifecycle states, persist transitions, return them sorted by score DESC."""
    # Fetch candidates with a score row for today
    as_of_start = datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC)
    as_of_end = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=UTC)

    scored_ids_result = await session.execute(
        select(CandidateScoreHistory.candidate_id)
        .where(CandidateScoreHistory.scored_at >= as_of_start)
        .where(CandidateScoreHistory.scored_at <= as_of_end)
        .distinct()
    )
    scored_ids = [r[0] for r in scored_ids_result.all()]

    if not scored_ids:
        return []

    candidates_result = await session.execute(
        select(OpportunityCandidate).where(OpportunityCandidate.id.in_(scored_ids))
    )
    candidates = candidates_result.scalars().all()

    transitions: list[LifecycleTransition] = []

    for candidate in candidates:
        # Fetch score history from last 14 days, oldest-first
        since = as_of - timedelta(days=14)
        history_result = await session.execute(
            select(CandidateScoreHistory)
            .where(CandidateScoreHistory.candidate_id == candidate.id)
            .where(CandidateScoreHistory.scored_at >= since)
            .order_by(CandidateScoreHistory.scored_at.asc())
        )
        history = history_result.scalars().all()

        old_state = candidate.lifecycle_state
        new_state = derive_lifecycle_state(candidate, history)

        if new_state == old_state:
            continue

        latest_score = history[-1].score_total if history else 0.0
        candidate.lifecycle_state = new_state

        if new_state is None:
            # State dropped to unclassified: record with NULL new_state, no alert
            session.add(LifecycleEvent(
                candidate_id=candidate.id,
                old_state=old_state,
                new_state=None,
                score_total=latest_score,
                was_alerted=False,
                recorded_at=as_of,
            ))
            continue

        event = LifecycleEvent(
            candidate_id=candidate.id,
            old_state=old_state,
            new_state=new_state,
            score_total=latest_score,
            was_alerted=False,
            recorded_at=as_of,
        )
        session.add(event)
        await session.flush()

        transitions.append(
            LifecycleTransition(
                candidate_id=candidate.id,
                old_state=old_state,
                new_state=new_state,
                score_total=latest_score,
                problem_statement=candidate.problem_statement,
                lifecycle_event_id=event.id,
            )
        )

    await session.commit()

    transitions.sort(key=lambda t: t.score_total, reverse=True)
    log.info("lifecycle_transitions", count=len(transitions))
    return transitions
