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
    if update.effective_message:
        await update.effective_message.reply_text(_COMING_SOON)


def register_command_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CommandHandler("briefing", briefing_handler))
    application.add_handler(CommandHandler("niches", niches_handler))
    application.add_handler(CommandHandler("niche", niche_handler))
    application.add_handler(CommandHandler("trending", trending_handler))
    application.add_handler(CommandHandler("sources", sources_handler))
