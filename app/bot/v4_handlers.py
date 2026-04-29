"""v4 bot command handlers: /opportunities, /opportunity, /categories, /category, /emerging."""
from __future__ import annotations

from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.formatter import (
    lifecycle_arrow,
    md_escape,
    score_breakdown_block,
    truncate,
)
from app.config import get_settings
from app.db import get_session
from app.models import (
    CandidateBrief,
    CandidateScoreHistory,
    CandidateValidation,
    Category,
    OpportunityCandidate,
    PainPoint,
    SourceItem,
)

_DEFAULT_TOP_N = 10
_MAX_MESSAGE_CHARS = 4096


def _candidate_inline_buttons(candidate_id: int, brief_id: int | None = None) -> list[list[InlineKeyboardButton]]:
    brief_str = str(brief_id) if brief_id is not None else "none"
    return [[
        InlineKeyboardButton("👍 useful", callback_data=f"fb:up:{candidate_id}"),
        InlineKeyboardButton("👎 not useful", callback_data=f"fb:down:{candidate_id}"),
        InlineKeyboardButton("📄 details", callback_data=f"view:{candidate_id}:{brief_str}"),
    ]]


async def _fetch_latest_score(session, candidate_id: int) -> CandidateScoreHistory | None:
    result = await session.execute(
        select(CandidateScoreHistory)
        .where(CandidateScoreHistory.candidate_id == candidate_id)
        .order_by(CandidateScoreHistory.scored_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _fetch_latest_brief(session, candidate_id: int) -> CandidateBrief | None:
    result = await session.execute(
        select(CandidateBrief)
        .where(CandidateBrief.candidate_id == candidate_id)
        .order_by(CandidateBrief.generated_at.desc())
        .limit(1)
    )
    return result.scalars().first()


def _render_candidate_card(
    c: OpportunityCandidate,
    rank: int,
    score: CandidateScoreHistory | None,
) -> str:
    title = md_escape(truncate(c.problem_statement or "", 80))
    score_str = md_escape(str(int(score.score_total + 0.5)) if score else "—")
    lc = lifecycle_arrow(c.lifecycle_state)
    lc_str = f"  {md_escape(lc)}" if lc else ""
    return f"\\#{rank} *{title}* — Score: {score_str}{lc_str}"


async def cmd_opportunities(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/opportunities [N] — Top N candidates by current score."""
    if not update.effective_message:
        return

    settings = get_settings()
    n = _DEFAULT_TOP_N
    if ctx.args:
        try:
            n = int(ctx.args[0])
        except (ValueError, IndexError):
            pass

    async with get_session() as session:
        subq = (
            select(
                CandidateScoreHistory.candidate_id,
                func.max(CandidateScoreHistory.score_total).label("max_score"),
            )
            .group_by(CandidateScoreHistory.candidate_id)
            .subquery()
        )
        result = await session.execute(
            select(OpportunityCandidate)
            .join(subq, OpportunityCandidate.id == subq.c.candidate_id)
            .where(OpportunityCandidate.is_archived.is_(False))
            .where(OpportunityCandidate.specificity > settings.specificity_gate)
            .order_by(subq.c.max_score.desc())
            .limit(n)
        )
        candidates = result.scalars().all()

    if not candidates:
        await update.effective_message.reply_text(
            "No opportunities yet — give the pipeline a few days to warm up\\.",
            parse_mode="MarkdownV2",
        )
        return

    async with get_session() as session:
        lines = ["*Top Opportunities*\n"]
        all_buttons: list[list[InlineKeyboardButton]] = []
        for i, c in enumerate(candidates, start=1):
            score = await _fetch_latest_score(session, c.id)
            brief = await _fetch_latest_brief(session, c.id)
            lines.append(_render_candidate_card(c, i, score))
            all_buttons.extend(_candidate_inline_buttons(c.id, brief.id if brief else None))

    text = truncate("\n".join(lines), _MAX_MESSAGE_CHARS, footer="\n\\.\\.\\. \\(truncated\\)")
    markup = InlineKeyboardMarkup(all_buttons)
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2", reply_markup=markup)


async def cmd_opportunity(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/opportunity <id> — Full scorecard for a candidate."""
    if not update.effective_message:
        return

    if not ctx.args:
        await update.effective_message.reply_text(
            "Usage: /opportunity \\<id\\>", parse_mode="MarkdownV2"
        )
        return

    try:
        candidate_id = int(ctx.args[0])
    except ValueError:
        await update.effective_message.reply_text("Please provide a numeric candidate id\\.", parse_mode="MarkdownV2")
        return

    settings = get_settings()

    async with get_session() as session:
        c_result = await session.execute(
            select(OpportunityCandidate).where(OpportunityCandidate.id == candidate_id)
        )
        c = c_result.scalars().first()

        if c is None:
            await update.effective_message.reply_text("Candidate not found\\.", parse_mode="MarkdownV2")
            return

        score = await _fetch_latest_score(session, c.id)
        brief = await _fetch_latest_brief(session, c.id)

        # Top 5 evidence excerpts
        pp_result = await session.execute(
            select(PainPoint, SourceItem)
            .join(SourceItem, PainPoint.source_item_id == SourceItem.id)
            .where(PainPoint.candidate_id == c.id)
            .order_by(PainPoint.extracted_at.desc())
            .limit(5)
        )
        evidence_rows = pp_result.all()

        # Latest validation
        val_result = await session.execute(
            select(CandidateValidation)
            .where(CandidateValidation.candidate_id == c.id)
            .where(CandidateValidation.signal_type == "composite")
            .order_by(CandidateValidation.validated_at.desc())
            .limit(1)
        )
        validation = val_result.scalars().first()

    lines: list[str] = []

    # Archived banner
    if c.is_archived:
        lines.append("⚠️ This opportunity has been archived\\.")
        lines.append("")

    # Below gate warning
    if c.specificity <= settings.specificity_gate:
        lines.append(
            "⚠️ This opportunity is below the specificity threshold and may not be actionable yet\\."
        )
        lines.append("")

    lc = lifecycle_arrow(c.lifecycle_state)
    title = md_escape(c.problem_statement or "")
    lines.append(f"*{title}*  {md_escape(lc) if lc else ''}")

    if c.audience:
        lines.append(f"Audience: {md_escape(c.audience)}")
    if c.why_now:
        lines.append(f"Why now: {md_escape(truncate(c.why_now, 200))}")
    lines.append("")

    if brief and brief.summary:
        lines.append(f"_{md_escape(truncate(brief.summary, 300))}_")
    else:
        lines.append("_Brief generates at digest time\\._")
    lines.append("")

    if score and score.score_breakdown_json:
        lines.append(score_breakdown_block(score.score_breakdown_json))
        lines.append("")

    if validation and validation.metadata_json:
        meta = validation.metadata_json
        repo_count = meta.get("repo_count", 0)
        lines.append(f"Validation: {md_escape(str(repo_count))} repos found on GitHub\\.")

    if evidence_rows:
        lines.append("\n*Evidence:*")
        for pp, si in evidence_rows:
            src = md_escape(si.source_type or "")
            excerpt = md_escape(truncate(pp.problem_text or "", 120))
            url_part = f" \\([link]({si.url})\\)" if si.url else ""
            lines.append(f"• \\[{src}\\] {excerpt}{url_part}")

    text = truncate("\n".join(lines), _MAX_MESSAGE_CHARS, footer="\n\\.\\.\\. see latest brief")
    markup = InlineKeyboardMarkup(
        _candidate_inline_buttons(c.id, brief.id if brief else None)
    )
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2", reply_markup=markup)


async def cmd_categories(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/categories — Overview by category with active-candidate counts."""
    if not update.effective_message:
        return

    settings = get_settings()

    async with get_session() as session:
        result = await session.execute(
            select(
                OpportunityCandidate.category_id,
                OpportunityCandidate.lifecycle_state,
                func.count(OpportunityCandidate.id).label("cnt"),
            )
            .where(OpportunityCandidate.is_archived.is_(False))
            .where(OpportunityCandidate.specificity > settings.specificity_gate)
            .group_by(
                OpportunityCandidate.category_id,
                OpportunityCandidate.lifecycle_state,
            )
        )
        rows = result.all()

        cat_result = await session.execute(select(Category))
        categories = {c.id: c for c in cat_result.scalars().all()}

    # Aggregate
    cat_data: dict[int | None, dict] = {}
    for row in rows:
        cid = row.category_id
        entry = cat_data.setdefault(cid, {"total": 0, "states": {}})
        entry["total"] += row.cnt
        if row.lifecycle_state:
            entry["states"][row.lifecycle_state] = (
                entry["states"].get(row.lifecycle_state, 0) + row.cnt
            )

    lines = ["📂 *Categories*\n"]
    for cat_id, data in sorted(cat_data.items(), key=lambda x: -(x[1]["total"])):
        cat = categories.get(cat_id)
        name = cat.name if cat else "Uncategorised"
        total = data["total"]
        state_parts = []
        for state in ("hot", "emerging", "saturated", "dormant"):
            cnt = data["states"].get(state, 0)
            if cnt:
                lc = lifecycle_arrow(state)
                state_parts.append(f"{cnt} {md_escape(lc)}")
        state_str = f" · {' · '.join(state_parts)}" if state_parts else ""
        lines.append(f"*{md_escape(name)}* — {md_escape(str(total))} active{md_escape(state_str)}")

    if len(lines) == 1:
        lines.append("No active opportunities yet\\.")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def cmd_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/category <slug> — Top candidates in a named category."""
    if not update.effective_message:
        return

    settings = get_settings()

    if not ctx.args:
        await update.effective_message.reply_text(
            "Usage: /category \\<slug\\>", parse_mode="MarkdownV2"
        )
        return

    slug = ctx.args[0].lower()

    async with get_session() as session:
        cat_result = await session.execute(
            select(Category).where(Category.slug == slug)
        )
        cat = cat_result.scalars().first()

        if cat is None:
            all_cats = await session.execute(select(Category.slug))
            slugs = ", ".join(sorted(r[0] for r in all_cats.all()))
            await update.effective_message.reply_text(
                f"Unknown category\\. Available: {md_escape(slugs)}",
                parse_mode="MarkdownV2",
            )
            return

        subq = (
            select(
                CandidateScoreHistory.candidate_id,
                func.max(CandidateScoreHistory.score_total).label("max_score"),
            )
            .group_by(CandidateScoreHistory.candidate_id)
            .subquery()
        )
        result = await session.execute(
            select(OpportunityCandidate)
            .join(subq, OpportunityCandidate.id == subq.c.candidate_id)
            .where(OpportunityCandidate.category_id == cat.id)
            .where(OpportunityCandidate.is_archived.is_(False))
            .where(OpportunityCandidate.specificity > settings.specificity_gate)
            .order_by(subq.c.max_score.desc())
            .limit(_DEFAULT_TOP_N)
        )
        candidates = result.scalars().all()

        lines = [f"*{md_escape(cat.name)}* candidates\n"]
        all_buttons: list[list[InlineKeyboardButton]] = []
        for i, c in enumerate(candidates, start=1):
            score = await _fetch_latest_score(session, c.id)
            brief = await _fetch_latest_brief(session, c.id)
            lines.append(_render_candidate_card(c, i, score))
            all_buttons.extend(_candidate_inline_buttons(c.id, brief.id if brief else None))

    if not candidates:
        await update.effective_message.reply_text(
            f"No active opportunities in *{md_escape(cat.name)}* yet\\.",
            parse_mode="MarkdownV2",
        )
        return

    markup = InlineKeyboardMarkup(all_buttons)
    text = truncate("\n".join(lines), _MAX_MESSAGE_CHARS, footer="\n\\.\\.\\. \\(truncated\\)")
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2", reply_markup=markup)


async def cmd_emerging(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/emerging — Newly-discovered opportunities (lifecycle_state='emerging')."""
    if not update.effective_message:
        return

    settings = get_settings()

    async with get_session() as session:
        result = await session.execute(
            select(OpportunityCandidate)
            .where(OpportunityCandidate.lifecycle_state == "emerging")
            .where(OpportunityCandidate.is_archived.is_(False))
            .where(OpportunityCandidate.specificity > settings.specificity_gate)
            .order_by(OpportunityCandidate.created_at.desc())
        )
        candidates = result.scalars().all()

    if not candidates:
        await update.effective_message.reply_text(
            "No emerging opportunities right now — check back after the next scoring run\\.",
            parse_mode="MarkdownV2",
        )
        return

    async with get_session() as session:
        lines = ["🌱 *Emerging Opportunities*\n"]
        all_buttons: list[list[InlineKeyboardButton]] = []
        for i, c in enumerate(candidates, start=1):
            score = await _fetch_latest_score(session, c.id)
            brief = await _fetch_latest_brief(session, c.id)
            lines.append(_render_candidate_card(c, i, score))
            all_buttons.extend(_candidate_inline_buttons(c.id, brief.id if brief else None))

    text = truncate("\n".join(lines), _MAX_MESSAGE_CHARS, footer="\n\\.\\.\\. \\(truncated\\)")
    markup = InlineKeyboardMarkup(all_buttons)
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2", reply_markup=markup)
