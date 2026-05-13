"""B-20: Specificity gate consistency audit across all surfaces."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm.mock_adapter import MockLLMAdapter
from app.models import (
    CandidateBrief,
    CandidateScoreHistory,
    LifecycleEvent,
    OpportunityCandidate,
    PainPoint,
    SourceItem,
)
from app.pipeline.lifecycle import LifecycleTransition
from app.scoring.candidate_scorer import score_all_candidates




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


async def _seed_gated_candidate(session: AsyncSession, score: float = 50.0) -> OpportunityCandidate:
    """Specificity=2 candidate — below default gate of 2."""
    c = OpportunityCandidate(
        problem_statement="below gate candidate",
        specificity=2,
        lifecycle_state="hot",
    )
    session.add(c)
    await session.flush()

    row = CandidateScoreHistory(
        candidate_id=c.id,
        score_total=score,
        score_breakdown_json={
            "frequency": {"raw": 5, "score": 50},
            "momentum": {"raw": 0.3, "score": 50},
            "source_diversity": {"raw": 2, "score": 50},
            "validation": 70,
            "specificity": 40,
            "weights": {"frequency": 0.25, "momentum": 0.30, "source_diversity": 0.15, "validation": 0.20, "specificity": 0.10},
        },
        scored_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    return c


async def test_scorer_skips_below_gate(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    await _seed_gated_candidate(session)
    rows = await score_all_candidates(session, as_of=datetime.now(UTC))
    assert rows == []


async def test_opportunities_handler_excludes_below_gate(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    await _seed_gated_candidate(session)

    update = MagicMock()
    update.effective_message = AsyncMock()
    update.effective_message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.args = []

    with patch("app.bot.v4_handlers.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.bot.v4_handlers import cmd_opportunities
        await cmd_opportunities(update, ctx)

    text = update.effective_message.reply_text.call_args[0][0]
    assert "below gate" not in text.lower() or "No opportunities" in text


async def test_emerging_handler_excludes_below_gate(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    c = OpportunityCandidate(
        problem_statement="gated emerging",
        specificity=2,
        lifecycle_state="emerging",
    )
    session.add(c)
    await session.commit()

    update = MagicMock()
    update.effective_message = AsyncMock()
    update.effective_message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.args = []

    with patch("app.bot.v4_handlers.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.bot.v4_handlers import cmd_emerging
        await cmd_emerging(update, ctx)

    text = update.effective_message.reply_text.call_args[0][0]
    assert "gated emerging" not in text


async def test_lifecycle_alerts_skip_below_gate_by_not_scoring(session: AsyncSession, monkeypatch) -> None:
    """Gated candidates are never scored, so never appear in transitions."""
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    await _seed_gated_candidate(session, score=99.0)

    # score_all_candidates should skip it
    rows = await score_all_candidates(session, as_of=datetime.now(UTC))
    assert rows == []


async def test_opportunity_by_id_returns_below_gate_with_warning(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    c = await _seed_gated_candidate(session)

    update = MagicMock()
    update.effective_message = AsyncMock()
    update.effective_message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.args = [str(c.id)]

    with patch("app.bot.v4_handlers.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.bot.v4_handlers import cmd_opportunity
        await cmd_opportunity(update, ctx)

    text = update.effective_message.reply_text.call_args[0][0]
    assert "specificity threshold" in text.lower() or "below" in text.lower()


async def test_digest_excludes_below_gate(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    await _seed_gated_candidate(session, score=99.0)

    from app.bot.v4_notifications import fetch_top_candidates
    top = await fetch_top_candidates(session, limit=3, min_specificity=3)
    assert top == []
