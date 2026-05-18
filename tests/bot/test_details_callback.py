"""Tests for app/bot/details.py — the 'view:' inline-button callback."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.details import cmd_view_callback
from app.models import OpportunityCandidate


def _make_callback_update(data: str, user_id: int = 42, chat_id: int = 99) -> MagicMock:
    """Mirror the test helper from tests/bot/test_feedback.py for consistency."""
    update = MagicMock()
    query = AsyncMock()
    query.data = data
    query.from_user = MagicMock(id=user_id)
    query.message = MagicMock()
    query.message.chat_id = chat_id
    query.message.reply_text = AsyncMock()
    query.answer = AsyncMock()
    update.callback_query = query
    update.effective_chat = MagicMock(id=chat_id)
    return update


async def test_view_callback_replies_with_opportunity_card(
    session: AsyncSession, monkeypatch
) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    c = OpportunityCandidate(
        problem_statement="A specific developer pain", specificity=3
    )
    session.add(c)
    await session.commit()

    update = _make_callback_update(f"view:{c.id}:none")
    ctx = MagicMock()

    with patch("app.bot.details.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)
        await cmd_view_callback(update, ctx)

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.message.reply_text.assert_awaited_once()
    sent_text = update.callback_query.message.reply_text.call_args.args[0]
    assert "A specific developer pain" in sent_text


async def test_view_callback_unknown_candidate(
    session: AsyncSession, monkeypatch
) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    update = _make_callback_update("view:99999:none")
    ctx = MagicMock()

    with patch("app.bot.details.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)
        await cmd_view_callback(update, ctx)

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.message.reply_text.assert_awaited_once()
    sent_text = update.callback_query.message.reply_text.call_args.args[0]
    assert "not found" in sent_text.lower()


async def test_view_callback_malformed_data_does_not_reply() -> None:
    update = _make_callback_update("view:")
    ctx = MagicMock()

    await cmd_view_callback(update, ctx)

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.message.reply_text.assert_not_awaited()
