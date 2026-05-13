"""Tests for Stage 8 — lifecycle.py"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CandidateScoreHistory,
    LifecycleEvent,
    OpportunityCandidate,
)
from app.pipeline.lifecycle import (
    LifecycleTransition,
    derive_lifecycle_state,
    update_lifecycle_states_and_emit_transitions,
)


def _score_row(candidate_id: int, momentum: float, frequency: float, total: float = 50.0, days_ago: int = 0) -> CandidateScoreHistory:
    return CandidateScoreHistory(
        candidate_id=candidate_id,
        score_total=total,
        score_breakdown_json={
            "momentum": {"raw": 0.4, "score": momentum},
            "frequency": {"raw": 5, "score": frequency},
            "source_diversity": {"raw": 2, "score": 50},
            "validation": 70,
            "specificity": 60,
        },
        scored_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


def _candidate(lifecycle_state: str | None = None, age_days: int = 5, last_evidence_days_ago: int | None = 5) -> OpportunityCandidate:
    c = OpportunityCandidate(
        problem_statement="test",
        specificity=3,
        lifecycle_state=lifecycle_state,
        created_at=datetime.now(UTC) - timedelta(days=age_days),
        last_evidence_at=(
            datetime.now(UTC) - timedelta(days=last_evidence_days_ago)
            if last_evidence_days_ago is not None else None
        ),
    )
    c.id = 1
    return c


def test_derive_emerging() -> None:
    c = _candidate(age_days=5, last_evidence_days_ago=5)
    history = [_score_row(1, momentum=70, frequency=20)]
    assert derive_lifecycle_state(c, history) == "emerging"


def test_derive_hot() -> None:
    c = _candidate(age_days=30, last_evidence_days_ago=5)
    history = [_score_row(1, momentum=70, frequency=40)]
    assert derive_lifecycle_state(c, history) == "hot"


def test_derive_saturated() -> None:
    c = _candidate(age_days=30, last_evidence_days_ago=5)
    history = [_score_row(1, momentum=20, frequency=80)]
    assert derive_lifecycle_state(c, history) == "saturated"


def test_derive_dormant_overrides_other_signals() -> None:
    c = _candidate(age_days=30, last_evidence_days_ago=20)
    history = [_score_row(1, momentum=70, frequency=40)]
    assert derive_lifecycle_state(c, history) == "dormant"


def test_derive_handles_null_last_evidence_at() -> None:
    c = _candidate(age_days=5, last_evidence_days_ago=None)
    history = [_score_row(1, momentum=70, frequency=20)]
    assert derive_lifecycle_state(c, history) == "emerging"


def test_derive_none_when_no_match() -> None:
    c = _candidate(age_days=30, last_evidence_days_ago=5)
    history = [_score_row(1, momentum=40, frequency=40)]
    assert derive_lifecycle_state(c, history) is None


async def test_update_emits_transition_only_on_change(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    c = OpportunityCandidate(
        problem_statement="test",
        specificity=3,
        lifecycle_state="emerging",
        created_at=now - timedelta(days=30),
        last_evidence_at=now - timedelta(days=1),
    )
    session.add(c)
    await session.flush()

    # Score that would still yield "hot" but candidate is already "hot"
    row = CandidateScoreHistory(
        candidate_id=c.id,
        score_total=80.0,
        score_breakdown_json={
            "momentum": {"raw": 0.4, "score": 70},
            "frequency": {"raw": 5, "score": 40},
            "source_diversity": {"raw": 2, "score": 50},
            "validation": 70,
            "specificity": 60,
        },
        scored_at=now,
    )
    c.lifecycle_state = "hot"
    session.add(row)
    await session.commit()

    transitions = await update_lifecycle_states_and_emit_transitions(session, as_of=now)
    assert transitions == []

    count = (await session.execute(select(func.count(LifecycleEvent.id)))).scalar_one()
    assert count == 0


async def test_update_emits_transition_on_change(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    c = OpportunityCandidate(
        problem_statement="test emerging to hot",
        specificity=3,
        lifecycle_state="emerging",
        created_at=now - timedelta(days=30),
        last_evidence_at=now - timedelta(days=1),
    )
    session.add(c)
    await session.flush()

    row = CandidateScoreHistory(
        candidate_id=c.id,
        score_total=85.0,
        score_breakdown_json={
            "momentum": {"raw": 0.5, "score": 70},
            "frequency": {"raw": 8, "score": 40},
            "source_diversity": {"raw": 3, "score": 60},
            "validation": 70,
            "specificity": 60,
        },
        scored_at=now,
    )
    session.add(row)
    await session.commit()

    transitions = await update_lifecycle_states_and_emit_transitions(session, as_of=now)
    assert len(transitions) == 1
    assert transitions[0].old_state == "emerging"
    assert transitions[0].new_state == "hot"

    count = (await session.execute(select(func.count(LifecycleEvent.id)))).scalar_one()
    assert count == 1

    evt = (await session.execute(select(LifecycleEvent))).scalars().first()
    assert evt.was_alerted is False


async def test_none_new_state_never_writes_empty_string(session: AsyncSession) -> None:
    """When derive returns None from a non-null old_state, LifecycleEvent.new_state is NULL, not ''."""
    from sqlalchemy import func
    now = datetime.now(UTC)
    c = OpportunityCandidate(
        problem_statement="was hot now uncertain",
        specificity=3,
        lifecycle_state="hot",
        created_at=now - timedelta(days=30),
        last_evidence_at=now - timedelta(days=1),
    )
    session.add(c)
    await session.flush()

    # momentum=40, frequency=40 — doesn't match any lifecycle state → derive returns None
    # Keep a strong reference so SQLAlchemy's weak-ref identity map doesn't GC it (which
    # would cause SQLite to return a naive datetime while candidate.created_at stays aware).
    score_row = CandidateScoreHistory(
        candidate_id=c.id,
        score_total=40.0,
        score_breakdown_json={
            "momentum": {"raw": 0.3, "score": 40},
            "frequency": {"raw": 4, "score": 40},
            "source_diversity": {"raw": 2, "score": 50},
            "validation": 50,
            "specificity": 60,
        },
        scored_at=now,
    )
    session.add(score_row)
    await session.commit()

    transitions = await update_lifecycle_states_and_emit_transitions(session, as_of=now)

    # No LifecycleTransition emitted (new_state=None transitions are not alerted)
    assert len(transitions) == 0

    bad_count = (await session.execute(
        select(func.count(LifecycleEvent.id)).where(LifecycleEvent.new_state == "")
    )).scalar_one()
    assert bad_count == 0


async def test_update_reads_recent_history_not_oldest_14(session: AsyncSession) -> None:
    """Seed 20 daily rows; lifecycle uses today's row on day 20, not the oldest 14."""
    now = datetime.now(UTC)
    c = OpportunityCandidate(
        problem_statement="history depth check",
        specificity=3,
        lifecycle_state=None,
        created_at=now - timedelta(days=30),
        last_evidence_at=now - timedelta(days=1),
    )
    session.add(c)
    await session.flush()

    # Keep strong refs so SQLAlchemy's weak-ref identity map doesn't GC them between
    # commit and the next SELECT (which would cause naive vs. aware datetime mismatch).
    history_rows = []

    # Days 19..1: low scores that derive to None lifecycle state
    for day_ago in range(19, 0, -1):
        row = CandidateScoreHistory(
            candidate_id=c.id,
            score_total=30.0,
            score_breakdown_json={
                "momentum": {"raw": 0.1, "score": 20},
                "frequency": {"raw": 1, "score": 20},
                "source_diversity": {"raw": 1, "score": 50},
                "validation": 50,
                "specificity": 60,
            },
            scored_at=now - timedelta(days=day_ago),
        )
        session.add(row)
        history_rows.append(row)

    # Today: hot scores (momentum >= 60, frequency >= 30)
    today_row = CandidateScoreHistory(
        candidate_id=c.id,
        score_total=85.0,
        score_breakdown_json={
            "momentum": {"raw": 0.5, "score": 70},
            "frequency": {"raw": 8, "score": 40},
            "source_diversity": {"raw": 3, "score": 60},
            "validation": 70,
            "specificity": 60,
        },
        scored_at=now,
    )
    session.add(today_row)
    history_rows.append(today_row)
    await session.commit()

    transitions = await update_lifecycle_states_and_emit_transitions(session, as_of=now)
    assert len(transitions) == 1
    assert transitions[0].new_state == "hot"


async def test_update_persists_new_state(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    # emerging requires: momentum >= 60, frequency < 30, age_days < 14
    c = OpportunityCandidate(
        problem_statement="persistence check",
        specificity=3,
        lifecycle_state=None,
        created_at=now - timedelta(days=5),
        last_evidence_at=now - timedelta(days=1),
    )
    session.add(c)
    await session.flush()

    row = CandidateScoreHistory(
        candidate_id=c.id,
        score_total=60.0,
        score_breakdown_json={
            "momentum": {"raw": 0.3, "score": 65},
            "frequency": {"raw": 3, "score": 20},
            "source_diversity": {"raw": 2, "score": 50},
            "validation": 70,
            "specificity": 60,
        },
        scored_at=now,
    )
    session.add(row)
    await session.commit()

    await update_lifecycle_states_and_emit_transitions(session, as_of=now)
    await session.refresh(c)
    assert c.lifecycle_state == "emerging"
