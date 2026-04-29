from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select

from app.config import get_settings
from app.bot.formatter import bold, format_score, md_escape, trend_arrow, truncate
from app.db import get_session
# TODO(Plan C): Niche and OpportunityBrief are v3 ORM models deleted from app/models.py.
# This module is not reachable in production (test_notifications.py is skipped).
# Decommission and rewrite against OpportunityCandidate in Plan C.
from app.models import Niche, OpportunityBrief  # type: ignore[attr-defined]


async def build_daily_digest() -> str | None:
    """Return MarkdownV2-formatted digest, or None if no briefs exist."""
    settings = get_settings()
    async with get_session() as session:
        rows = (await session.execute(
            select(OpportunityBrief, Niche)
            .join(Niche, Niche.id == OpportunityBrief.niche_id)
            .order_by(OpportunityBrief.score_total.desc())
            .limit(settings.digest_top_n)
        )).all()

    if not rows:
        return None

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"🚀 {bold(f'DevTrend Daily Brief — {today}')}",
    ]
    for i, (brief, niche) in enumerate(rows, start=1):
        arrow = trend_arrow(brief.forecast_label)
        lines.append(
            f"\n{bold(f'#{i} — {niche.name}')} {arrow} "
            f"\\| {bold(format_score(brief.score_total))}\n"
            f"{md_escape(brief.summary)}"
        )

    return truncate("\n".join(lines), settings.telegram_max_message_chars)


def build_spike_alert(
    niche: Niche, today_score: float, prior_score: float
) -> str:
    delta = today_score - prior_score
    return "\n".join([
        f"⚡ {bold('Spike Alert')}",
        f"{bold(niche.name)}",
        f"Score: {bold(format_score(today_score))} "
        f"\\(was {format_score(prior_score)}, "
        f"\\+{format_score(delta)}\\)",
    ])
