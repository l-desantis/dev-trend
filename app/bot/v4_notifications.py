"""v4 push notifications: daily digest and lifecycle transition alerts."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from app.bot.formatter import lifecycle_arrow, md_escape, truncate
from app.config import Settings
from app.models import (
    CandidateBrief,
    CandidateScoreHistory,
    LifecycleEvent,
    OpportunityCandidate,
)

if TYPE_CHECKING:
    from app.pipeline.lifecycle import LifecycleTransition
    from app.llm.base import LLMAdapter

log = structlog.get_logger(__name__)


async def fetch_top_candidates(
    session: AsyncSession,
    *,
    limit: int = 3,
    min_specificity: int = 3,
) -> list[OpportunityCandidate]:
    """Fetch top-N candidates by latest score, above specificity gate."""
    now = datetime.now(UTC)
    today_start = datetime(now.year, now.month, now.day, tzinfo=UTC)

    subq = (
        select(
            CandidateScoreHistory.candidate_id,
            func.max(CandidateScoreHistory.score_total).label("max_score"),
        )
        .where(CandidateScoreHistory.scored_at >= today_start)
        .group_by(CandidateScoreHistory.candidate_id)
        .subquery()
    )

    result = await session.execute(
        select(OpportunityCandidate)
        .join(subq, OpportunityCandidate.id == subq.c.candidate_id)
        .where(OpportunityCandidate.is_archived.is_(False))
        .where(OpportunityCandidate.specificity >= min_specificity)
        .order_by(subq.c.max_score.desc())
        .limit(limit)
    )
    return result.scalars().all()


def _candidate_card(
    candidate: OpportunityCandidate,
    rank: int,
    brief: CandidateBrief | None,
    score: float | None,
) -> str:
    title = md_escape((candidate.problem_statement or "")[:80])
    score_str = md_escape(str(int(score + 0.5)) if score is not None else "—")
    lc = lifecycle_arrow(candidate.lifecycle_state)
    lc_str = md_escape(lc) if lc else ""

    parts = [f"\\#{rank} — *{title}* — Score: *{score_str}*"]
    if lc_str:
        parts[0] += f"  {lc_str}"

    if brief and brief.summary:
        excerpt = md_escape(truncate(brief.summary, 120))
        parts.append(f'"{excerpt}"')

    if candidate.audience:
        parts.append(f"Audience: {md_escape(candidate.audience)}")

    return "\n".join(parts)


def build_digest_message(
    candidates: list[OpportunityCandidate],
    briefs: list[CandidateBrief],
    *,
    scores: dict[int, float] | None = None,
    date: datetime | None = None,
    overflow_count: int = 0,
) -> str:
    if date is None:
        date = datetime.now(UTC)

    date_str = md_escape(date.strftime("%d %b %Y"))
    lines = [f"🚀 *DevTrend Daily Brief* — {date_str}\n"]

    brief_map = {b.candidate_id: b for b in briefs}

    for i, c in enumerate(candidates, start=1):
        brief = brief_map.get(c.id)
        score = (scores or {}).get(c.id)
        card = _candidate_card(c, i, brief, score)
        lines.append(card)
        if i < len(candidates):
            lines.append("")

    if overflow_count > 0:
        lines.append(
            f"\n\\+{md_escape(str(overflow_count))} other transitions overnight, "
            "see /opportunities\\."
        )

    return "\n".join(lines)


def build_digest_buttons(
    candidates: list[OpportunityCandidate],
    briefs: list[CandidateBrief],
) -> InlineKeyboardMarkup:
    brief_map = {b.candidate_id: b for b in briefs}
    rows = []
    for c in candidates:
        brief = brief_map.get(c.id)
        brief_id_str = str(brief.id) if brief else "none"
        rows.append([
            InlineKeyboardButton("👍 useful", callback_data=f"fb:up:{c.id}"),
            InlineKeyboardButton("👎 not useful", callback_data=f"fb:down:{c.id}"),
            InlineKeyboardButton("📄 details", callback_data=f"view:{c.id}:{brief_id_str}"),
        ])
    return InlineKeyboardMarkup(rows)


async def _count_overflow_transitions(session: AsyncSession) -> int:
    since = datetime.now(UTC) - timedelta(hours=24)
    result = await session.execute(
        select(func.count(LifecycleEvent.id))
        .where(LifecycleEvent.recorded_at >= since)
        .where(LifecycleEvent.was_alerted.is_(False))
        .where(LifecycleEvent.new_state.in_(["emerging", "hot", "saturated"]))
    )
    return result.scalar_one()


async def _fetch_scores_for(
    session: AsyncSession, candidate_ids: list[int]
) -> dict[int, float]:
    if not candidate_ids:
        return {}
    result = await session.execute(
        select(
            CandidateScoreHistory.candidate_id,
            func.max(CandidateScoreHistory.score_total).label("max_score"),
        )
        .where(CandidateScoreHistory.candidate_id.in_(candidate_ids))
        .group_by(CandidateScoreHistory.candidate_id)
    )
    return {row.candidate_id: row.max_score for row in result.all()}


async def run_digest_job(
    session_factory: Any,
    bot: Bot,
    llm: "LLMAdapter",
    settings: Settings,
) -> None:
    from app.pipeline.brief_generation import generate_briefs_for

    async with session_factory() as session:
        min_specificity = settings.specificity_gate + 1
        top = await fetch_top_candidates(session, limit=settings.digest_top_n, min_specificity=min_specificity)
        if not top:
            log.info("digest_no_candidates")
            return

        briefs = await generate_briefs_for(session, llm, top)
        scores = await _fetch_scores_for(session, [c.id for c in top])
        overflow = await _count_overflow_transitions(session)

    text = build_digest_message(top, briefs, scores=scores, overflow_count=overflow)
    markup = build_digest_buttons(top, briefs)

    for chat_id in settings.telegram_allowed_chat_ids:
        try:
            await bot.send_message(
                chat_id, text, parse_mode="MarkdownV2", reply_markup=markup
            )
        except TelegramError as e:
            log.warning("digest_send_failed", chat_id=chat_id, error=str(e))


async def emit_lifecycle_alerts(
    transitions: "list[LifecycleTransition]",
    bot: Bot,
    session: AsyncSession,
    chat_ids: list[int],
    settings: Settings,
) -> int:
    """Push up to settings.max_alerts_per_day lifecycle alerts sorted by score DESC.

    Sets was_alerted=True on pushed LifecycleEvent rows. Returns send count.
    """
    alertable = [t for t in transitions if t.new_state in ("emerging", "hot", "saturated")]
    alertable.sort(key=lambda t: t.score_total, reverse=True)

    to_push = alertable[: settings.max_alerts_per_day]
    sends = 0

    for transition in to_push:
        text = _build_alert_text(transition)
        markup = _build_alert_buttons(transition)
        for chat_id in chat_ids:
            try:
                await bot.send_message(
                    chat_id, text, parse_mode="MarkdownV2", reply_markup=markup
                )
                sends += 1
            except TelegramError as exc:
                log.warning(
                    "lifecycle_alert_send_failed",
                    chat_id=chat_id,
                    candidate_id=transition.candidate_id,
                    error=str(exc),
                )

        # Mark lifecycle_event as alerted
        result = await session.execute(
            select(LifecycleEvent)
            .where(LifecycleEvent.candidate_id == transition.candidate_id)
            .where(LifecycleEvent.new_state == transition.new_state)
            .where(LifecycleEvent.was_alerted.is_(False))
            .order_by(LifecycleEvent.recorded_at.desc())
            .limit(1)
        )
        evt = result.scalars().first()
        if evt:
            evt.was_alerted = True

    await session.commit()
    return sends


def _build_alert_text(transition: "LifecycleTransition") -> str:
    lc = lifecycle_arrow(transition.new_state)
    heading = md_escape(lc or transition.new_state.title())
    title = md_escape(truncate(transition.problem_statement, 80))
    score_str = md_escape(str(int(transition.score_total + 0.5)))
    old_str = md_escape(transition.old_state or "none")
    new_str = md_escape(transition.new_state)

    return (
        f"{heading}\n\n"
        f"*{title}*\n"
        f"Score: {score_str} — {old_str} → {new_str}\n"
    )


def _build_alert_buttons(transition: "LifecycleTransition") -> InlineKeyboardMarkup:
    cid = transition.candidate_id
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 useful", callback_data=f"fb:up:{cid}"),
        InlineKeyboardButton("👎 not useful", callback_data=f"fb:down:{cid}"),
        InlineKeyboardButton("📄 details", callback_data=f"view:{cid}:none"),
    ]])
