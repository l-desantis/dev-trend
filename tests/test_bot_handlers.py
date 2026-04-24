import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from telegram.ext import ApplicationHandlerStop

from app.bot.handlers import help_handler, sources_handler, start_handler
from app.bot.middleware import _allowlist_check
from app.db import init_db
from app.ingestion.base import ConnectorRunRegistry, RunStatus


def _make_update(chat_id: int | None) -> MagicMock:
    update = MagicMock()
    if chat_id is None:
        update.effective_chat = None
    else:
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat_id
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    return update


@pytest.fixture
def mock_context() -> MagicMock:
    return MagicMock()


class TestAllowlistMiddleware:
    async def test_allowed_chat_passes_through(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=12345)
        with patch("app.bot.middleware.get_settings") as mock_settings:
            mock_settings.return_value.telegram_allowed_chat_ids = [12345]
            await _allowlist_check(update, mock_context)

    async def test_unknown_chat_raises_stop(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=99999)
        with patch("app.bot.middleware.get_settings") as mock_settings:
            mock_settings.return_value.telegram_allowed_chat_ids = [12345]
            with pytest.raises(ApplicationHandlerStop):
                await _allowlist_check(update, mock_context)

    async def test_unknown_chat_sends_restriction_message(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=99999)
        with patch("app.bot.middleware.get_settings") as mock_settings:
            mock_settings.return_value.telegram_allowed_chat_ids = [12345]
            with pytest.raises(ApplicationHandlerStop):
                await _allowlist_check(update, mock_context)
        update.effective_message.reply_text.assert_called_once_with(
            "This bot is private. Access is restricted."
        )

    async def test_none_effective_chat_raises_stop(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=None)
        with pytest.raises(ApplicationHandlerStop):
            await _allowlist_check(update, mock_context)


class TestStartHandler:
    async def test_replies_with_markdownv2(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=12345)
        await start_handler(update, mock_context)
        update.effective_message.reply_text.assert_called_once()
        assert update.effective_message.reply_text.call_args.kwargs["parse_mode"] == "MarkdownV2"

    async def test_reply_contains_devtrend(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=12345)
        await start_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "DevTrend" in text

    async def test_no_error_when_no_message(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=12345)
        update.effective_message = None
        await start_handler(update, mock_context)


class TestHelpHandler:
    async def test_replies_with_markdownv2(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=12345)
        await help_handler(update, mock_context)
        update.effective_message.reply_text.assert_called_once()
        assert update.effective_message.reply_text.call_args.kwargs["parse_mode"] == "MarkdownV2"

    async def test_reply_contains_start_command(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=12345)
        await help_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "/start" in text

    async def test_reply_contains_help_command(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=12345)
        await help_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "/help" in text

    async def test_no_error_when_no_message(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=12345)
        update.effective_message = None
        await help_handler(update, mock_context)


class TestSourcesHandler:
    async def test_empty_registry_never_run(self, mock_context: MagicMock) -> None:
        await init_db()
        mock_context.application = MagicMock()
        mock_context.application.bot_data = {}
        update = _make_update(chat_id=12345)
        await sources_handler(update, mock_context)
        update.effective_message.reply_text.assert_called_once()
        text = update.effective_message.reply_text.call_args.args[0]
        assert "never run" in text

    async def test_ok_status_shows_items(self, mock_context: MagicMock) -> None:
        await init_db()
        registry = ConnectorRunRegistry()
        registry.mark_running("github")
        registry.mark_success("github", items=42, duration=1.5)

        mock_context.application = MagicMock()
        mock_context.application.bot_data = {"run_registry": registry}
        update = _make_update(chat_id=12345)
        await sources_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "42" in text
        assert "github" in text
