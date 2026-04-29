"""Tests verifying that allowlist middleware gates callback queries (B-18)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ApplicationHandlerStop

from app.bot.middleware import _allowlist_check


def _make_callback_update(chat_id: int) -> MagicMock:
    """Simulate a CallbackQuery update — effective_chat resolves from the message's chat."""
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_message = AsyncMock()
    update.effective_message.reply_text = AsyncMock()

    # Attach a callback_query to distinguish this from a plain message update
    update.callback_query = MagicMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.chat = MagicMock()
    update.callback_query.message.chat.id = chat_id
    return update


async def test_middleware_blocks_callback_from_unallowed_chat() -> None:
    update = _make_callback_update(chat_id=99999)
    ctx = MagicMock()

    with patch("app.bot.middleware.get_settings") as mock_settings:
        mock_settings.return_value.telegram_allowed_chat_ids = [12345]
        with pytest.raises(ApplicationHandlerStop):
            await _allowlist_check(update, ctx)


async def test_middleware_allows_callback_from_allowed_chat() -> None:
    update = _make_callback_update(chat_id=12345)
    ctx = MagicMock()

    with patch("app.bot.middleware.get_settings") as mock_settings:
        mock_settings.return_value.telegram_allowed_chat_ids = [12345]
        # Should not raise
        await _allowlist_check(update, ctx)
