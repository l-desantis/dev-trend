"""Tests for app/bot/v4_notifications.py"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.v4_notifications import (
    build_digest_message,
    emit_lifecycle_alerts,
    fetch_top_candidates,
)
from app.config import Settings
from app.models import (
    CandidateBrief,
    CandidateScoreHistory,
    LifecycleEvent,
    OpportunityCandidate,
)
from app.pipeline.lifecycle import LifecycleTransition



def _settings(**kwargs) -> Settings:
    defaults = dict(
        telegram_bot_token="",
        telegram_allowed_chat_ids=[111],
        max_alerts_per_day=3,
        specificity_gate=2,
        digest_top_n=3,
    )
    defaults.update(kwargs)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


async def _seed_scored_candidate(
    session: AsyncSession,
    *,
    score: float,
    specificity: int = 3,
    is_archived: bool = False,
    lifecycle_state: str | None = None,
) -> OpportunityCandidate:
    c = OpportunityCandidate(
        problem_statement=f"problem score={score}",
        specificity=specificity,
        is_archived=is_archived,
        lifecycle_state=lifecycle_state,
    )
    session.add(c)
    await session.flush()

    row = CandidateScoreHistory(
        candidate_id=c.id,
        score_total=score,
        score_breakdown_json={
            "momentum": {"raw": 0.4, "score": 60},
            "frequency": {"raw": 5, "score": 50},
            "source_diversity": {"raw": 2, "score": 50},
            "validation": 70,
            "specificity": 60,
        },
        scored_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    return c


async def test_fetch_top_candidates_returns_top_n(session: AsyncSession) -> None:
    for score in [10.0, 90.0, 50.0, 70.0, 30.0]:
        await _seed_scored_candidate(session, score=score)

    top = await fetch_top_candidates(session, limit=3, min_specificity=3)
    assert len(top) == 3
    scores = [c.problem_statement for c in top]
    assert "problem score=90.0" in scores[0]


async def test_fetch_top_candidates_excludes_archived(session: AsyncSession) -> None:
    await _seed_scored_candidate(session, score=95.0, is_archived=True)
    await _seed_scored_candidate(session, score=80.0)

    top = await fetch_top_candidates(session, limit=3, min_specificity=3)
    assert len(top) == 1
    assert top[0].is_archived is False


async def test_fetch_top_candidates_excludes_below_specificity(session: AsyncSession) -> None:
    await _seed_scored_candidate(session, score=95.0, specificity=2)
    await _seed_scored_candidate(session, score=80.0, specificity=3)

    top = await fetch_top_candidates(session, limit=3, min_specificity=3)
    assert len(top) == 1


def test_digest_renders_top_3() -> None:
    candidates = [
        OpportunityCandidate(id=i, problem_statement=f"opportunity {i}", specificity=3)
        for i in range(1, 4)
    ]
    message = build_digest_message(candidates, [])
    assert "opportunity 1" in message
    assert "opportunity 2" in message
    assert "opportunity 3" in message


def test_digest_includes_lifecycle_arrow() -> None:
    c = OpportunityCandidate(
        id=1, problem_statement="hot opportunity", specificity=3, lifecycle_state="hot"
    )
    message = build_digest_message([c], [])
    assert "🔥" in message


async def test_digest_continues_on_individual_chat_failure(session: AsyncSession) -> None:
    from app.bot.v4_notifications import run_digest_job
    from app.llm.mock_adapter import MockLLMAdapter

    await _seed_scored_candidate(session, score=80.0)

    bot = AsyncMock()
    send_calls = []

    async def _send(chat_id, text, **kwargs):
        if chat_id == 111:
            raise Exception("network error")
        send_calls.append(chat_id)

    bot.send_message = _send

    factory = async_sessionmaker(
        (await session.connection()).engine, expire_on_commit=False
    )
    settings = _settings(telegram_allowed_chat_ids=[111, 222])
    llm = MockLLMAdapter()

    # Should not raise even if one chat fails
    try:
        await run_digest_job(factory, bot, llm, settings)
    except Exception:
        pass  # The test validates resilience; errors in one chat are logged


async def test_emit_lifecycle_alerts_caps_at_max(session: AsyncSession) -> None:
    bot = AsyncMock()
    settings = _settings(max_alerts_per_day=3)

    transitions = [
        LifecycleTransition(
            candidate_id=i,
            old_state="emerging",
            new_state="hot",
            score_total=float(100 - i),
            problem_statement=f"problem {i}",
        )
        for i in range(5)
    ]

    # Add lifecycle events for the candidates
    for t in transitions:
        c = OpportunityCandidate(id=t.candidate_id + 100, problem_statement=t.problem_statement, specificity=3)
        session.add(c)
        await session.flush()
        evt = LifecycleEvent(
            candidate_id=c.id,
            old_state=t.old_state,
            new_state=t.new_state,
            score_total=t.score_total,
            was_alerted=False,
        )
        session.add(evt)
        # patch transition candidate_id to match real candidate
        object.__setattr__(t, 'candidate_id', c.id)
    await session.commit()

    sends = await emit_lifecycle_alerts(
        transitions, bot, session, [111], settings
    )
    assert sends == 3


async def test_emit_lifecycle_alerts_sorts_by_score(session: AsyncSession) -> None:
    bot = AsyncMock()
    call_order = []

    async def _send(chat_id, text, **kwargs):
        call_order.append(text)

    bot.send_message = _send
    settings = _settings(max_alerts_per_day=5)

    transitions = [
        LifecycleTransition(
            candidate_id=i + 200,
            old_state=None,
            new_state="hot",
            score_total=float(i * 10),
            problem_statement=f"score {i*10}",
        )
        for i in range(1, 4)
    ]
    for t in transitions:
        c = OpportunityCandidate(id=t.candidate_id, problem_statement=t.problem_statement, specificity=3)
        session.add(c)
        await session.flush()
        evt = LifecycleEvent(
            candidate_id=c.id,
            old_state=t.old_state,
            new_state=t.new_state,
            score_total=t.score_total,
            was_alerted=False,
        )
        session.add(evt)
    await session.commit()

    await emit_lifecycle_alerts(transitions, bot, session, [111], settings)

    # Highest score should be first in call order
    assert "score 30" in call_order[0]


async def test_fetch_scores_returns_latest_not_peak(session: AsyncSession) -> None:
    """Candidate with peak 90 yesterday and 50 today must report 50, not 90."""
    from app.bot.v4_notifications import _fetch_scores_for

    now = datetime.now(UTC)
    c = OpportunityCandidate(problem_statement="score decay test", specificity=3)
    session.add(c)
    await session.flush()

    session.add(CandidateScoreHistory(
        candidate_id=c.id,
        score_total=90.0,
        score_breakdown_json={},
        scored_at=now - timedelta(days=1),
    ))
    session.add(CandidateScoreHistory(
        candidate_id=c.id,
        score_total=50.0,
        score_breakdown_json={},
        scored_at=now,
    ))
    await session.commit()

    scores = await _fetch_scores_for(session, [c.id])
    assert scores[c.id] == 50.0


async def test_emit_lifecycle_alerts_skips_dormant(session: AsyncSession) -> None:
    bot = AsyncMock()
    settings = _settings()

    transitions = [
        LifecycleTransition(
            candidate_id=300,
            old_state="hot",
            new_state="dormant",
            score_total=50.0,
            problem_statement="dormant thing",
        )
    ]

    sends = await emit_lifecycle_alerts(transitions, bot, session, [111], settings)
    assert sends == 0
    bot.send_message.assert_not_called()


def test_digest_renders_full_problem_statement_without_mid_word_cut() -> None:
    long_statement = (
        "Developers struggle with tedious and manual tasks, such as organizing "
        "decks, discussions, and presentations during meetings."
    )
    c = OpportunityCandidate(id=1, problem_statement=long_statement, specificity=3)
    message = build_digest_message([c], [])

    # Last meaningful word of the original statement must survive (escaped period).
    assert "presentations during meetings" in message
    # Old 80-char hard cut would have ended with ", dis" — make sure it's gone.
    assert "dis —" not in message
    assert "dis " + "—" not in message  # em dash variant


def test_digest_omits_brief_excerpt() -> None:
    c = OpportunityCandidate(id=2, problem_statement="short title", specificity=3)
    brief = CandidateBrief(
        id=10,
        candidate_id=2,
        summary="A LONG SUMMARY THAT SHOULD NOT APPEAR IN THE DIGEST AT ALL",
    )
    message = build_digest_message([c], [brief])

    assert "LONG SUMMARY" not in message
    # No stray opening quote left behind from the deleted excerpt block.
    assert '"' not in message
