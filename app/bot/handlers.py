from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

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
    if update.effective_message:
        await update.effective_message.reply_text(_COMING_SOON)


async def niches_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(_COMING_SOON)


async def niche_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(_COMING_SOON)


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
