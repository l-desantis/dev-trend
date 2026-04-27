import pytest
from datetime import datetime, timedelta, timezone

from app.db import get_session, init_db


@pytest.fixture
async def setup_db():
    await init_db()


class TestDailyDigest:
    async def test_digest_includes_top_n_briefs(self, setup_db):
        from app.bot.notifications import build_daily_digest
        from app.models import Niche, OpportunityBrief

        async with get_session() as s:
            n = Niche(name="Habits", slug="habits", category="c", keywords_json=[])
            s.add(n); await s.flush()
            s.add(OpportunityBrief(
                niche_id=n.id, headline="Habits — 84",
                summary="Up.", score_total=84.0,
                score_breakdown_json={}, evidence_json=[],
                forecast_label="Rising", has_issues=False,
                generated_at=datetime.now(timezone.utc), model_name="qwen2.5",
            ))
            await s.commit()

        text = await build_daily_digest()
        assert text is not None
        assert "Habits" in text
        assert "84" in text
        assert "↑" in text

    async def test_digest_returns_none_when_empty(self, setup_db):
        from app.bot.notifications import build_daily_digest
        text = await build_daily_digest()
        assert text is None


class TestSpikeAlert:
    async def test_spike_alert_message_includes_delta(self, setup_db):
        from app.bot.notifications import build_spike_alert
        from app.models import Niche

        async with get_session() as s:
            n = Niche(name="Boom", slug="boom", category="c", keywords_json=[])
            s.add(n); await s.commit()
            await s.refresh(n)
            text = build_spike_alert(
                niche=n, today_score=80.0, prior_score=60.0,
            )
        assert "Boom" in text
        assert "80" in text
        assert "20" in text  # delta
