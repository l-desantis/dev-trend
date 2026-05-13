"""Bot handler tests — v4 trimmed."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram.ext import ApplicationHandlerStop

from app.bot.handlers import help_handler, sources_handler, start_handler
from app.bot.middleware import _allowlist_check
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

    async def test_reply_lists_v4_commands(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=12345)
        await help_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "/opportunities" in text
        assert "/emerging" in text
        assert "coming soon" not in text.lower()

    async def test_no_error_when_no_message(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=12345)
        update.effective_message = None
        await help_handler(update, mock_context)


class TestSourcesHandler:
    async def test_empty_registry_never_run(self, mock_context: MagicMock) -> None:

        mock_context.application = MagicMock()
        mock_context.application.bot_data = {}
        update = _make_update(chat_id=12345)
        await sources_handler(update, mock_context)
        update.effective_message.reply_text.assert_called_once()
        text = update.effective_message.reply_text.call_args.args[0]
        assert "never run" in text

    async def test_ok_status_shows_items(self, mock_context: MagicMock) -> None:

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


class TestBotCommandMenuRegistered:
    def test_only_trimmed_commands_registered(self) -> None:
        from app.bot.handlers import register_command_handlers
        from telegram.ext import Application, CommandHandler
        from unittest.mock import MagicMock

        app = MagicMock(spec=Application)
        register_command_handlers(app)

        registered = [
            cmd
            for call in app.add_handler.call_args_list
            if isinstance(call.args[0], CommandHandler)
            for cmd in call.args[0].commands
        ]
        assert "start" in registered
        assert "help" in registered
        assert "sources" in registered
        assert "opportunities" in registered
        assert "opportunity" in registered
        assert "categories" in registered
        assert "category" in registered
        assert "emerging" in registered
        # v3 commands must be absent
        assert "briefing" not in registered
        assert "niches" not in registered
        assert "niche" not in registered
        assert "trending" not in registered


class TestAllowlistSecurityPath:
    async def test_unknown_chat_rejected_and_no_downstream(self, mock_context: MagicMock) -> None:
        update = _make_update(chat_id=99999)
        with patch("app.bot.middleware.get_settings") as mock_settings:
            mock_settings.return_value.telegram_allowed_chat_ids = [12345]
            with pytest.raises(ApplicationHandlerStop):
                await _allowlist_check(update, mock_context)
        update.effective_message.reply_text.assert_called_once_with(
            "This bot is private. Access is restricted."
        )


class TestSourcesRegistryTimestamp:
    async def test_sources_shows_last_run_timestamp(self, mock_context: MagicMock) -> None:
        from datetime import datetime, timezone


        registry = ConnectorRunRegistry()
        registry.mark_running("github")
        registry.mark_success("github", items=10, duration=2.0)
        expected_ts = datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc)
        registry._statuses["github"].last_run_at = expected_ts

        mock_context.application = MagicMock()
        mock_context.application.bot_data = {"run_registry": registry}
        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        await sources_handler(update, mock_context)

        text = update.effective_message.reply_text.call_args.args[0]
        assert "2026\\-04\\-20" in text
        assert "09:00" in text
