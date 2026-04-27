from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from app.bot.notifications import build_daily_digest, build_spike_alert
from app.config import get_settings
from app.db import get_session
from app.models import Niche, NicheScoreHistory

log = structlog.get_logger(__name__)


async def push_daily_digest(bot) -> None:
    if bot is None:
        log.warning("digest_skipped", reason="no_bot")
        return
    settings = get_settings()
    chats = settings.telegram_allowed_chat_ids or []
    if not chats:
        log.warning("digest_skipped", reason="no_allowed_chats")
        return

    text = await build_daily_digest()
    if text is None:
        log.info("digest_skipped", reason="no_briefs")
        return

    for chat_id in chats:
        try:
            await bot.send_message(
                chat_id=chat_id, text=text, parse_mode="MarkdownV2"
            )
            log.info("digest_pushed", chat_id=chat_id, length=len(text))
        except Exception as exc:
            log.error("digest_push_failed", chat_id=chat_id, error=str(exc))


async def push_spike_alerts(bot, as_of: datetime | None = None) -> None:
    if bot is None:
        log.warning("spike_skipped", reason="no_bot")
        return
    settings = get_settings()
    chats = settings.telegram_allowed_chat_ids or []
    if not chats:
        log.warning("spike_skipped", reason="no_allowed_chats")
        return

    when = as_of or datetime.now(timezone.utc)
    today_start = when.replace(hour=0, minute=0, second=0, microsecond=0)

    alerts: list[str] = []
    async with get_session() as session:
        niches = (await session.execute(select(Niche))).scalars().all()
        for niche in niches:
            today = (await session.execute(
                select(NicheScoreHistory)
                .where(NicheScoreHistory.niche_id == niche.id)
                .where(NicheScoreHistory.scored_at >= today_start)
                .order_by(NicheScoreHistory.scored_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if today is None:
                continue
            prior = (await session.execute(
                select(NicheScoreHistory)
                .where(NicheScoreHistory.niche_id == niche.id)
                .where(NicheScoreHistory.scored_at < today_start)
                .order_by(NicheScoreHistory.scored_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if prior is None:
                continue
            delta = today.score_total - prior.score_total
            if delta >= settings.spike_alert_threshold:
                alerts.append(build_spike_alert(
                    niche=niche,
                    today_score=today.score_total,
                    prior_score=prior.score_total,
                ))

    if not alerts:
        log.info("spike_skipped", reason="no_alerts")
        return

    for chat_id in chats:
        for text in alerts:
            try:
                await bot.send_message(
                    chat_id=chat_id, text=text, parse_mode="MarkdownV2"
                )
                log.info("spike_pushed", chat_id=chat_id)
            except Exception as exc:
                log.error("spike_push_failed", chat_id=chat_id, error=str(exc))
