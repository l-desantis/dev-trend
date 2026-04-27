from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.config import get_settings

_START_TEXT = (
    "👋 *Welcome to DevTrend\\!*\n\n"
    "I monitor developer\\-facing market signals across GitHub, Hacker News, Reddit, "
    "and app stores — then synthesise them into structured opportunity briefs\\.\n\n"
    "Use /help to see all available commands\\."
)

_HELP_TEXT = (
    "/start — Welcome message and feature overview\n"
    "/briefing — On\\-demand top 3 opportunity briefs\n"
    "/niches — List all tracked niches with current scores\n"
    "/niche \\<slug\\> — Full scorecard and evidence for a specific niche\n"
    "/trending — Top rising signals across all sources in last 24h\n"
    "/sources — Last ingestion timestamp and status per source\n"
    "/help — Show this message"
)


_COMING_SOON = "⚙️ This command is not yet available. Check back soon."


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(_START_TEXT, parse_mode="MarkdownV2")


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(_HELP_TEXT, parse_mode="MarkdownV2")


async def briefing_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return top-N latest opportunity briefs, ranked by score_total."""
    from sqlalchemy import select
    from app.db import get_session
    from app.models import Niche, OpportunityBrief
    from app.bot.formatter import bold, format_score, md_escape, trend_arrow, truncate

    settings = get_settings()
    async with get_session() as session:
        result = await session.execute(
            select(OpportunityBrief, Niche)
            .join(Niche, Niche.id == OpportunityBrief.niche_id)
            .order_by(OpportunityBrief.score_total.desc())
            .limit(settings.briefing_top_n)
        )
        rows = result.all()

    if not rows:
        await update.effective_message.reply_text(
            md_escape("No briefs yet — the agent will run at 03:00 UTC."),
            parse_mode="MarkdownV2",
        )
        return

    lines = [bold("DevTrend Briefing")]
    for i, (brief, niche) in enumerate(rows, start=1):
        arrow = trend_arrow(brief.forecast_label)
        lines.append(
            f"\n{i}\\. {bold(niche.name)} "
            f"\\| {bold(format_score(brief.score_total))} {arrow}\n"
            f"{md_escape(brief.summary)}"
        )

    text = truncate("\n".join(lines), settings.telegram_max_message_chars)
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2")


async def niches_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from sqlalchemy import select, func
    from app.db import get_session
    from app.models import Niche, NicheScoreHistory, OpportunityBrief
    from app.bot.formatter import bold, format_score, md_escape, trend_arrow, truncate

    settings = get_settings()

    async with get_session() as session:
        latest_score = (
            select(
                NicheScoreHistory.niche_id,
                func.max(NicheScoreHistory.scored_at).label("max_at"),
            )
            .group_by(NicheScoreHistory.niche_id)
            .subquery()
        )
        score_rows = await session.execute(
            select(NicheScoreHistory)
            .join(
                latest_score,
                (NicheScoreHistory.niche_id == latest_score.c.niche_id)
                & (NicheScoreHistory.scored_at == latest_score.c.max_at),
            )
        )
        scores = {r[0].niche_id: r[0].score_total for r in score_rows.all()}

        latest_brief = (
            select(
                OpportunityBrief.niche_id,
                func.max(OpportunityBrief.generated_at).label("max_at"),
            )
            .group_by(OpportunityBrief.niche_id)
            .subquery()
        )
        brief_rows = await session.execute(
            select(OpportunityBrief)
            .join(
                latest_brief,
                (OpportunityBrief.niche_id == latest_brief.c.niche_id)
                & (OpportunityBrief.generated_at == latest_brief.c.max_at),
            )
        )
        labels = {r[0].niche_id: r[0].forecast_label for r in brief_rows.all()}

        niches = (await session.execute(select(Niche))).scalars().all()

    ranked = sorted(niches, key=lambda n: scores.get(n.id, 0.0), reverse=True)

    if not ranked:
        await update.effective_message.reply_text(
            md_escape("No niches loaded."), parse_mode="MarkdownV2"
        )
        return

    lines = [bold("Tracked Niches")]
    for n in ranked:
        score = scores.get(n.id, 0.0)
        arrow = trend_arrow(labels.get(n.id, "Stable"))
        lines.append(
            f"{arrow} {bold(n.name)} \\| {bold(format_score(score))} "
            f"\\(`/niche {md_escape(n.slug)}`\\)"
        )

    text = truncate("\n".join(lines), settings.telegram_max_message_chars)
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2")


async def niche_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from sqlalchemy import select
    from app.db import get_session
    from app.models import Niche, OpportunityBrief
    from app.bot.formatter import bold, format_score, md_escape, trend_arrow, truncate

    settings = get_settings()
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            md_escape("Usage: /niche <slug>  (try /niches for the list)"),
            parse_mode="MarkdownV2",
        )
        return

    slug = args[0].strip().lower()

    async with get_session() as session:
        niche = (await session.execute(
            select(Niche).where(Niche.slug == slug)
        )).scalar_one_or_none()

        if niche is None:
            await update.effective_message.reply_text(
                md_escape(f"Niche '{slug}' not found."),
                parse_mode="MarkdownV2",
            )
            return

        brief = (await session.execute(
            select(OpportunityBrief)
            .where(OpportunityBrief.niche_id == niche.id)
            .order_by(OpportunityBrief.generated_at.desc())
            .limit(1)
        )).scalar_one_or_none()

    if brief is None:
        await update.effective_message.reply_text(
            f"{bold(niche.name)}\n{md_escape('No brief yet.')}",
            parse_mode="MarkdownV2",
        )
        return

    breakdown = brief.score_breakdown_json or {}
    arrow = trend_arrow(brief.forecast_label)
    lines = [
        f"{bold(niche.name)} {arrow}",
        f"{bold('Score')}: {bold(format_score(brief.score_total))}",
        f"  Growth: {format_score(breakdown.get('growth', 0))}",
        f"  Demand: {format_score(breakdown.get('demand', 0))}",
        f"  Novelty: {format_score(breakdown.get('novelty', 0))}",
        "",
        md_escape(brief.summary),
    ]

    evidence = brief.evidence_json or []
    if evidence:
        lines.append("")
        lines.append(bold("Evidence"))
        for e in evidence[:5]:
            title = md_escape(e.get("title", "(untitled)"))
            url = md_escape(e.get("url", ""))
            stype = md_escape(e.get("source_type", "?"))
            lines.append(f"\\- \\[{stype}\\] {title} {url}")

    text = truncate(
        "\n".join(lines),
        settings.telegram_max_message_chars,
        footer="…\n_\\(truncated\\)_",
    )
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2")


async def trending_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(_COMING_SOON)


async def sources_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return

    from datetime import UTC
    from sqlalchemy import func, select
    from app.db import get_session
    from app.models import SourceItem
    from app.ingestion.base import ConnectorRunRegistry, RunStatus
    from app.bot.formatter import md_escape

    registry: ConnectorRunRegistry | None = context.application.bot_data.get("run_registry")
    source_types = ["github", "hn", "reddit", "appstore"]
    lines = ["*Sources — last ingestion status*\n"]

    for st in source_types:
        status: RunStatus | None = registry.get(st) if registry else None

        if status is None or status.last_status == "never":
            async with get_session() as session:
                row = await session.execute(
                    select(func.max(SourceItem.ingested_at), func.count(SourceItem.id))
                    .where(SourceItem.source_type == st)
                )
                max_at, count = row.one()
            if max_at:
                ts = md_escape(max_at.strftime("%Y-%m-%d %H:%M UTC"))
                lines.append(f"*{md_escape(st)}* — DB: {count} items, last at {ts}")
            else:
                lines.append(f"*{md_escape(st)}* — never run")
        else:
            emoji = {"ok": "✅", "error": "⚠️", "running": "🔄"}.get(status.last_status, "❓")
            ts = md_escape(status.last_run_at.strftime("%Y-%m-%d %H:%M UTC")) if status.last_run_at else "unknown"
            dur = f"{status.duration_s:.1f}s" if status.duration_s else "—"
            line = f"{emoji} *{md_escape(st)}* — {status.items_ingested} items in {md_escape(dur)} at {ts}"
            if status.error:
                line += f"\n  _{md_escape(status.error[:80])}_"
            lines.append(line)

    await update.effective_message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


def register_command_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CommandHandler("briefing", briefing_handler))
    application.add_handler(CommandHandler("niches", niches_handler))
    application.add_handler(CommandHandler("niche", niche_handler))
    application.add_handler(CommandHandler("trending", trending_handler))
    application.add_handler(CommandHandler("sources", sources_handler))
