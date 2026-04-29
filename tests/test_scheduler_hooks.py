import pytest
pytest.skip("v3 — scheduler hooks removed in Plan A; deferred to Plan C", allow_module_level=True)

from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta, timezone

from app.db import get_session, init_db


@pytest.fixture
async def setup_db():
    await init_db()


class TestPushDailyDigest:
    async def test_pushes_to_all_allowed_chats(self, setup_db):
        from app.bot.scheduler_hooks import push_daily_digest
        from app.models import Niche, OpportunityBrief

        async with get_session() as s:
            n = Niche(name="X", slug="x", category="c", keywords_json=[])
            s.add(n); await s.flush()
            s.add(OpportunityBrief(
                niche_id=n.id, headline="h", summary="s", score_total=80.0,
                score_breakdown_json={}, evidence_json=[],
                forecast_label="Rising", has_issues=False,
                generated_at=datetime.now(timezone.utc), model_name="qwen2.5",
            ))
            await s.commit()

        bot = AsyncMock()
        with patch("app.bot.scheduler_hooks.get_settings") as mock_s:
            mock_s.return_value.telegram_allowed_chat_ids = [1, 2, 3]
            mock_s.return_value.digest_top_n = 3
            mock_s.return_value.telegram_max_message_chars = 4096
            await push_daily_digest(bot)
        assert bot.send_message.await_count == 3

    async def test_skips_when_no_briefs(self, setup_db):
        from app.bot.scheduler_hooks import push_daily_digest
        bot = AsyncMock()
        with patch("app.bot.scheduler_hooks.get_settings") as mock_s:
            mock_s.return_value.telegram_allowed_chat_ids = [1]
            mock_s.return_value.digest_top_n = 3
            mock_s.return_value.telegram_max_message_chars = 4096
            await push_daily_digest(bot)
        bot.send_message.assert_not_awaited()


class TestPushSpikeAlerts:
    async def test_pushes_when_delta_above_threshold(self, setup_db):
        from app.bot.scheduler_hooks import push_spike_alerts
        from app.models import Niche, NicheScoreHistory

        now = datetime.now(timezone.utc)
        async with get_session() as s:
            n = Niche(name="Spike", slug="spike", category="c", keywords_json=[])
            s.add(n); await s.flush()
            s.add_all([
                NicheScoreHistory(niche_id=n.id, score_total=50.0,
                                  score_breakdown_json={},
                                  scored_at=now - timedelta(days=1)),
                NicheScoreHistory(niche_id=n.id, score_total=80.0,
                                  score_breakdown_json={}, scored_at=now),
            ])
            await s.commit()

        bot = AsyncMock()
        with patch("app.bot.scheduler_hooks.get_settings") as mock_s:
            mock_s.return_value.telegram_allowed_chat_ids = [1]
            mock_s.return_value.spike_alert_threshold = 15.0
            mock_s.return_value.telegram_max_message_chars = 4096
            await push_spike_alerts(bot, as_of=now)
        bot.send_message.assert_awaited_once()

    async def test_skips_when_below_threshold(self, setup_db):
        from app.bot.scheduler_hooks import push_spike_alerts
        from app.models import Niche, NicheScoreHistory

        now = datetime.now(timezone.utc)
        async with get_session() as s:
            n = Niche(name="Tiny", slug="tiny", category="c", keywords_json=[])
            s.add(n); await s.flush()
            s.add_all([
                NicheScoreHistory(niche_id=n.id, score_total=70.0,
                                  score_breakdown_json={},
                                  scored_at=now - timedelta(days=1)),
                NicheScoreHistory(niche_id=n.id, score_total=72.0,
                                  score_breakdown_json={}, scored_at=now),
            ])
            await s.commit()

        bot = AsyncMock()
        with patch("app.bot.scheduler_hooks.get_settings") as mock_s:
            mock_s.return_value.telegram_allowed_chat_ids = [1]
            mock_s.return_value.spike_alert_threshold = 15.0
            mock_s.return_value.telegram_max_message_chars = 4096
            await push_spike_alerts(bot, as_of=now)
        bot.send_message.assert_not_awaited()

    async def test_no_op_when_bot_is_none(self, setup_db):
        from app.bot.scheduler_hooks import push_spike_alerts
        # Should not raise
        await push_spike_alerts(None)
