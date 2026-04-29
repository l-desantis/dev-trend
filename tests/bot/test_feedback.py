"""Tests for app/bot/feedback.py"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot.feedback import cmd_feedback_callback
from app.models import Base, CandidateFeedback, OpportunityCandidate


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _make_callback_update(data: str, user_id: int = 42, chat_id: int = 99, brief_id: str = "none") -> MagicMock:
    update = MagicMock()
    query = AsyncMock()
    query.data = data
    query.from_user = MagicMock(id=user_id)
    query.message = MagicMock()
    query.message.chat_id = chat_id
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()

    # Wire the [📄 details] button so _extract_brief_id_from_message can find it
    btn_details = MagicMock()
    btn_details.callback_data = f"view:1:{brief_id}"
    query.message.reply_markup = MagicMock()
    query.message.reply_markup.inline_keyboard = [[btn_details]]

    update.callback_query = query
    return update


async def test_feedback_inserts_row(session: AsyncSession) -> None:
    c = OpportunityCandidate(problem_statement="test", specificity=3)
    session.add(c)
    await session.commit()

    update = _make_callback_update(f"fb:up:{c.id}")
    ctx = MagicMock()

    with patch("app.bot.feedback.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)
        await cmd_feedback_callback(update, ctx)

    result = await session.execute(select(CandidateFeedback))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].label == "up"
    assert rows[0].user_id == 42


async def test_feedback_flips_on_re_click(session: AsyncSession) -> None:
    c = OpportunityCandidate(problem_statement="test flip", specificity=3)
    session.add(c)
    await session.commit()

    ctx = MagicMock()

    with patch("app.bot.feedback.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

        update1 = _make_callback_update(f"fb:up:{c.id}")
        await cmd_feedback_callback(update1, ctx)

        update2 = _make_callback_update(f"fb:down:{c.id}")
        await cmd_feedback_callback(update2, ctx)

    result = await session.execute(select(CandidateFeedback))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].label == "down"


async def test_feedback_per_user_independent(session: AsyncSession) -> None:
    c = OpportunityCandidate(problem_statement="multi user", specificity=3)
    session.add(c)
    await session.commit()

    ctx = MagicMock()

    with patch("app.bot.feedback.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

        update_a = _make_callback_update(f"fb:up:{c.id}", user_id=1)
        await cmd_feedback_callback(update_a, ctx)

        update_b = _make_callback_update(f"fb:down:{c.id}", user_id=2)
        await cmd_feedback_callback(update_b, ctx)

    result = await session.execute(select(CandidateFeedback))
    rows = result.scalars().all()
    assert len(rows) == 2
    labels = {r.user_id: r.label for r in rows}
    assert labels[1] == "up"
    assert labels[2] == "down"


async def test_feedback_unknown_candidate_ignored_gracefully(session: AsyncSession) -> None:
    update = _make_callback_update("fb:up:99999")
    ctx = MagicMock()

    with patch("app.bot.feedback.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)
        # Should not raise
        await cmd_feedback_callback(update, ctx)

    result = await session.execute(select(CandidateFeedback))
    rows = result.scalars().all()
    assert len(rows) == 0
