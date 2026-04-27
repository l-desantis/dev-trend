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


class TestBriefingHandler:
    async def test_briefing_returns_top_n_briefs(self, mock_context):
        from app.bot.handlers import briefing_handler
        from app.db import get_session, init_db
        from app.models import Niche, OpportunityBrief
        from datetime import datetime, timezone

        await init_db()
        async with get_session() as s:
            niche = Niche(name="X", slug="x", category="c", keywords_json=[])
            s.add(niche); await s.flush()
            s.add(OpportunityBrief(
                niche_id=niche.id,
                headline="X — Score 84",
                summary="Strong momentum.",
                score_total=84.0,
                score_breakdown_json={"growth": 90, "demand": 80, "novelty": 70},
                evidence_json=[],
                forecast_label="Rising",
                has_issues=False,
                generated_at=datetime.now(timezone.utc),
                model_name="qwen2.5",
            ))
            await s.commit()

        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        await briefing_handler(update, mock_context)

        text = update.effective_message.reply_text.call_args.args[0]
        assert "84" in text
        assert "↑" in text
        assert "X" in text

    async def test_briefing_handles_no_briefs(self, mock_context):
        from app.bot.handlers import briefing_handler
        from app.db import init_db

        await init_db()
        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        await briefing_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "no briefs" in text.lower() or "not yet" in text.lower()


class TestNichesHandler:
    async def test_niches_lists_all_with_scores(self, mock_context):
        from app.bot.handlers import niches_handler
        from app.db import get_session, init_db
        from app.models import Niche, NicheScoreHistory
        from datetime import datetime, timezone

        await init_db()
        async with get_session() as s:
            n1 = Niche(name="Alpha", slug="alpha", category="c", keywords_json=[])
            n2 = Niche(name="Beta", slug="beta", category="c", keywords_json=[])
            s.add_all([n1, n2]); await s.flush()
            s.add_all([
                NicheScoreHistory(niche_id=n1.id, score_total=70.0,
                                  score_breakdown_json={}, scored_at=datetime.now(timezone.utc)),
                NicheScoreHistory(niche_id=n2.id, score_total=85.0,
                                  score_breakdown_json={}, scored_at=datetime.now(timezone.utc)),
            ])
            await s.commit()

        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        await niches_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "Alpha" in text and "Beta" in text
        # Beta listed first (higher score)
        assert text.index("Beta") < text.index("Alpha")


class TestNicheHandler:
    async def test_niche_returns_full_scorecard(self, mock_context):
        from app.bot.handlers import niche_handler
        from app.db import get_session, init_db
        from app.models import Niche, OpportunityBrief
        from datetime import datetime, timezone

        await init_db()
        async with get_session() as s:
            n = Niche(name="Alpha", slug="alpha", category="c",
                      keywords_json=[], summary="An alpha niche.")
            s.add(n); await s.flush()
            s.add(OpportunityBrief(
                niche_id=n.id, headline="Alpha — Score 80",
                summary="Strong week.", score_total=80.0,
                score_breakdown_json={"growth": 85, "demand": 78, "novelty": 75},
                evidence_json=[{"source_type": "github", "title": "repo-x",
                                "url": "https://x", "excerpt": "a repo"}],
                forecast_label="Rising", has_issues=False,
                generated_at=datetime.now(timezone.utc), model_name="qwen2.5",
            ))
            await s.commit()

        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        mock_context.args = ["alpha"]
        await niche_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "Alpha" in text
        assert "80" in text
        assert "Growth" in text or "growth" in text
        assert "repo" in text  # title escaped: repo\-x

    async def test_niche_unknown_slug(self, mock_context):
        from app.bot.handlers import niche_handler
        from app.db import init_db

        await init_db()
        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        mock_context.args = ["does-not-exist"]
        await niche_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0].lower()
        assert "not found" in text or "unknown" in text

    async def test_niche_no_args(self, mock_context):
        from app.bot.handlers import niche_handler
        from app.db import init_db

        await init_db()
        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        mock_context.args = []
        await niche_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0].lower()
        assert "usage" in text or "/niche" in text
