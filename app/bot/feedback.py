"""Inline button feedback callback handler: 👍/👎."""
from __future__ import annotations

from datetime import UTC, datetime

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.db import get_session
from app.models import CandidateFeedback, OpportunityCandidate

log = structlog.get_logger(__name__)


def _replace_with_confirmation(label: str) -> InlineKeyboardMarkup:
    text = "✓ Marked useful" if label == "up" else "✓ Marked not useful"
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data="noop")]])


def _extract_brief_id_from_message(message) -> int | None:
    """Read brief_id from the [📄 details] button callback_data: 'view:<cid>:<brief_id>'."""
    if message is None:
        return None
    try:
        reply_markup = message.reply_markup
        if reply_markup is None:
            return None
        for row in reply_markup.inline_keyboard:
            for btn in row:
                data = btn.callback_data or ""
                if data.startswith("view:"):
                    parts = data.split(":")
                    if len(parts) >= 3 and parts[2] not in ("none", ""):
                        return int(parts[2])
    except Exception:
        pass
    return None


async def cmd_feedback_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'fb:up:<id>' and 'fb:down:<id>' inline button callbacks."""
    query = update.callback_query
    if query is None:
        return

    await query.answer(text="Thanks — recorded.", show_alert=False)

    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "fb" or parts[1] not in ("up", "down"):
        log.warning("invalid_feedback_callback_data", data=data)
        return

    label = parts[1]
    try:
        candidate_id = int(parts[2])
    except ValueError:
        return

    user_id = query.from_user.id if query.from_user else 0
    chat_id = query.message.chat_id if query.message else 0
    brief_id = _extract_brief_id_from_message(query.message)

    async with get_session() as session:
        # Verify candidate exists
        from sqlalchemy import and_, select
        c_result = await session.execute(
            select(OpportunityCandidate).where(OpportunityCandidate.id == candidate_id)
        )
        if c_result.scalars().first() is None:
            log.warning("feedback_unknown_candidate", candidate_id=candidate_id)
            return

        # SQLite treats NULL as distinct in UNIQUE constraints, so on_conflict_do_update
        # does not fire when brief_id is NULL. Use manual SELECT + upsert instead.
        if brief_id is not None:
            existing_q = select(CandidateFeedback).where(
                and_(
                    CandidateFeedback.candidate_id == candidate_id,
                    CandidateFeedback.user_id == user_id,
                    CandidateFeedback.brief_id == brief_id,
                )
            )
        else:
            existing_q = select(CandidateFeedback).where(
                and_(
                    CandidateFeedback.candidate_id == candidate_id,
                    CandidateFeedback.user_id == user_id,
                    CandidateFeedback.brief_id.is_(None),
                )
            )

        existing = (await session.execute(existing_q)).scalars().first()
        if existing:
            existing.label = label
            existing.created_at = datetime.now(UTC)
        else:
            session.add(CandidateFeedback(
                candidate_id=candidate_id,
                user_id=user_id,
                chat_id=chat_id,
                brief_id=brief_id,
                label=label,
                created_at=datetime.now(UTC),
            ))
        await session.commit()

    try:
        await query.edit_message_reply_markup(
            reply_markup=_replace_with_confirmation(label)
        )
    except Exception as exc:
        log.warning("feedback_edit_markup_failed", error=str(exc))
