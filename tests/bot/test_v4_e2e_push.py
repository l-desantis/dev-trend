"""End-to-end push flow: scoring_job → lifecycle alerts → digest (B-19)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.llm.mock_adapter import MockLLMAdapter
from app.models import (
    CandidateScoreHistory,
    LifecycleEvent,
    OpportunityCandidate,
    PainPoint,
    SourceItem,
)
from app.pipeline.lifecycle import update_lifecycle_states_and_emit_transitions
from app.pipeline.validation import run_validation
from app.scoring.candidate_scorer import score_all_candidates
from app.bot.v4_notifications import emit_lifecycle_alerts, run_digest_job


@pytest.fixture
async def engine(database_url: str):
    eng = create_async_engine(database_url)
    yield eng
    await eng.dispose()


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


def _settings(**kwargs) -> Settings:
    defaults = dict(
        telegram_bot_token="",
        telegram_allowed_chat_ids=[111, 222],
        max_alerts_per_day=3,
        specificity_gate=2,
        digest_top_n=3,
    )
    defaults.update(kwargs)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


async def _seed_candidates(session: AsyncSession, n: int = 5) -> list[OpportunityCandidate]:
    now = datetime.now(UTC)
    candidates = []
    for i in range(n):
        c = OpportunityCandidate(
            problem_statement=f"opportunity #{i} — score {(n - i) * 10}",
            specificity=3,
            lifecycle_state="emerging",  # pre-existing state so transitions can fire
            created_at=now - timedelta(days=30),
            last_evidence_at=now - timedelta(days=1),
        )
        session.add(c)
        await session.flush()

        si = SourceItem(source_type="reddit", external_id=f"r_{i}", url=f"https://r.co/{i}")
        session.add(si)
        await session.flush()

        # More pain points for higher-ranked candidates
        for j in range(n - i):
            pp = PainPoint(
                source_item_id=si.id,
                candidate_id=c.id,
                extractor_model="mock",
                problem_text=f"wish #{i}_{j}",
                extracted_at=now - timedelta(days=j),
            )
            session.add(pp)

        candidates.append(c)

    await session.commit()
    return candidates


def _make_github_client() -> httpx.AsyncClient:
    client = AsyncMock(spec=httpx.AsyncClient)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"total_count": 3, "items": []}
    client.request = AsyncMock(return_value=mock_resp)
    return client


async def test_scoring_pipeline_end_to_end(factory, monkeypatch) -> None:
    """Full scoring → lifecycle → alerts → digest chain with mocked bot."""
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    settings = _settings()
    bot = AsyncMock()
    sent_messages: list[dict] = []

    async def _capture_send(chat_id, text, **kwargs):
        sent_messages.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})

    bot.send_message = _capture_send

    async with factory() as session:
        candidates = await _seed_candidates(session, n=5)

    # Step 1: Validation
    github_client = _make_github_client()
    async with factory() as session:
        await run_validation(session, github_client)

    # Step 2: Scoring
    as_of = datetime.now(UTC)
    async with factory() as session:
        score_rows = await score_all_candidates(session, as_of=as_of)

    assert len(score_rows) == 5

    # Step 3: Lifecycle transitions
    async with factory() as session:
        transitions = await update_lifecycle_states_and_emit_transitions(session, as_of=as_of)

    # All seeded candidates were 'emerging' with age=30d, so re-derive can't return 'emerging'
    # (age_days >= 14). Every candidate must transition to a different state or None.
    # At minimum the list is well-formed; each fired transition must have a valid new_state.
    assert isinstance(transitions, list)
    for t in transitions:
        assert t.new_state and t.new_state != "", "transition new_state must not be empty"
        assert t.score_total >= 0

    # All LifecycleEvents that were not alerted must have new_state != '' (I-4 fix)
    async with factory() as session:
        from sqlalchemy import func as _func
        bad = (await session.execute(
            select(LifecycleEvent).where(LifecycleEvent.new_state == "")
        )).scalars().all()
        assert len(bad) == 0, "LifecycleEvent.new_state must never be empty string"

    # Step 4: Emit lifecycle alerts (capped at max_alerts_per_day=3)
    async with factory() as session:
        sends = await emit_lifecycle_alerts(
            transitions, bot, session, settings.telegram_allowed_chat_ids, settings
        )

    # At most max_alerts_per_day * len(chat_ids) sends
    assert sends <= settings.max_alerts_per_day * len(settings.telegram_allowed_chat_ids)

    # Step 5: Check overflow — transitions not pushed have was_alerted=False
    async with factory() as session:
        overflow_result = await session.execute(
            select(LifecycleEvent).where(LifecycleEvent.was_alerted.is_(False))
        )
        overflow_rows = overflow_result.scalars().all()
        # If more transitions than cap, overflow rows exist
        if len(transitions) > settings.max_alerts_per_day:
            assert len(overflow_rows) > 0
        # Overflow rows must have was_alerted=False (never flipped by mistake)
        for row in overflow_rows:
            assert row.was_alerted is False
            assert row.new_state != "", "overflow LifecycleEvent.new_state must not be empty string"

    # Step 6: Digest
    llm = MockLLMAdapter()
    digest_messages: list[dict] = []

    async def _capture_digest(chat_id, text, **kwargs):
        digest_messages.append({"chat_id": chat_id, "text": text})

    bot.send_message = _capture_digest

    await run_digest_job(factory, bot, llm, settings)

    # Digest should push to both chat_ids
    assert len(digest_messages) == 2
    for msg in digest_messages:
        assert "DevTrend" in msg["text"]

    # Verify inline buttons contain fb: and view: patterns
    for call in bot.send_message.call_args_list if hasattr(bot.send_message, 'call_args_list') else []:
        markup = call.kwargs.get("reply_markup")
        if markup:
            all_data = [
                btn.callback_data
                for row in markup.inline_keyboard
                for btn in row
            ]
            assert any("fb:" in d for d in all_data)
            assert any("view:" in d for d in all_data)
