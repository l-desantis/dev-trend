"""Inline-button 'view:' callback — opens the full opportunity scorecard."""
from __future__ import annotations

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.v4_handlers import _render_opportunity_card
from app.config import get_settings
from app.db import get_session

log = structlog.get_logger(__name__)


async def cmd_view_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'view:<candidate_id>:<brief_id|none>' inline-button callbacks.

    Replies with the full opportunity card (same renderer as /opportunity).
    The brief_id segment is preserved in the contract but not consumed here —
    the renderer always fetches the latest brief by candidate_id.
    """
    query = update.callback_query
    if query is None:
        return

    await query.answer()

    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "view":
        log.warning("cb_view_malformed", data=data)
        return

    try:
        candidate_id = int(parts[1])
    except ValueError:
        log.warning("cb_view_malformed", data=data)
        return

    log.info(
        "cb_view",
        chat_id=query.message.chat_id if query.message else None,
        candidate_id=candidate_id,
    )

    settings = get_settings()

    async with get_session() as session:
        rendered = await _render_opportunity_card(session, candidate_id, settings)

    if query.message is None:
        return

    if rendered is None:
        await query.message.reply_text(
            "Candidate not found\\.", parse_mode="MarkdownV2"
        )
        return

    text, markup = rendered
    await query.message.reply_text(
        text, parse_mode="MarkdownV2", reply_markup=markup
    )
