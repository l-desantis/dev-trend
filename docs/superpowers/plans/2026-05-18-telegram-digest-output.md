# Telegram Digest Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two defects in the Telegram bot output: stop mid-word truncation of digest cards, and wire the silently-dead "📄 details" inline button to render the full opportunity scorecard.

**Architecture:** (1) Trim `_candidate_card` in `app/bot/v4_notifications.py` to render the full problem statement and drop the redundant brief excerpt. (2) Extract the body of `cmd_opportunity` in `app/bot/v4_handlers.py` into a pure renderer helper `_render_opportunity_card`. (3) Add a new `cmd_view_callback` in `app/bot/details.py` that consumes `^view:` callbacks (already produced by every emitter) and replies via the shared renderer. (4) Register the `^view:` `CallbackQueryHandler` in `app/bot/handlers.py`.

**Tech Stack:** Python 3.11+, `python-telegram-bot`, SQLAlchemy 2 (async), pytest with `asyncio`, structlog.

**Spec:** `docs/superpowers/specs/2026-05-18-telegram-digest-output-design.md`

**Environment note:** This project runs on Windows/WSL2 with `uv`. The implementer cannot invoke `uv`/`python`/`pytest` directly — paste the command, ask the user to run it, and wait for output.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `app/bot/v4_notifications.py` | Modify | Daily-digest message + lifecycle alerts. Trim `_candidate_card`. |
| `app/bot/v4_handlers.py` | Modify | Command handlers. Extract `_render_opportunity_card` helper; thin `cmd_opportunity` wraps it. |
| `app/bot/details.py` | **Create** | `cmd_view_callback` — handles `^view:` inline-button taps by re-using `_render_opportunity_card`. |
| `app/bot/handlers.py` | Modify | Register the new `CallbackQueryHandler(pattern=r"^view:")`. |
| `tests/bot/test_v4_notifications.py` | Modify | +2 tests for full-title rendering and absence of excerpt. |
| `tests/bot/test_details_callback.py` | **Create** | 3 tests for happy path / unknown candidate / malformed data. |
| `tests/bot/test_handlers_registration.py` | **Create** | Regression guard: assert `^view:` handler is registered. |

---

## Task 1 — Trim the digest card (full title, no excerpt)

**Files:**
- Modify: `app/bot/v4_notifications.py:60-82` (`_candidate_card`)
- Test: `tests/bot/test_v4_notifications.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/bot/test_v4_notifications.py` (the file already imports `CandidateBrief` and `OpportunityCandidate`):

```python
def test_digest_renders_full_problem_statement_without_mid_word_cut() -> None:
    long_statement = (
        "Developers struggle with tedious and manual tasks, such as organizing "
        "decks, discussions, and presentations during meetings."
    )
    c = OpportunityCandidate(id=1, problem_statement=long_statement, specificity=3)
    message = build_digest_message([c], [])

    # Last meaningful word of the original statement must survive (escaped period).
    assert "presentations during meetings" in message
    # Old 80-char hard cut would have ended with ", dis" — make sure it's gone.
    assert "dis —" not in message
    assert "dis " + "—" not in message  # em dash variant


def test_digest_omits_brief_excerpt() -> None:
    c = OpportunityCandidate(id=2, problem_statement="short title", specificity=3)
    brief = CandidateBrief(
        id=10,
        candidate_id=2,
        summary="A LONG SUMMARY THAT SHOULD NOT APPEAR IN THE DIGEST AT ALL",
    )
    message = build_digest_message([c], [brief])

    assert "LONG SUMMARY" not in message
    # No stray opening quote left behind from the deleted excerpt block.
    assert '"' not in message
```

- [ ] **Step 2: Run tests to verify they fail**

Ask the user to run:

```
! uv run pytest tests/bot/test_v4_notifications.py::test_digest_renders_full_problem_statement_without_mid_word_cut tests/bot/test_v4_notifications.py::test_digest_omits_brief_excerpt -v
```

Expected: both FAIL — the first because the current `[:80]` slice cuts the statement at "dis"; the second because the current code emits the quoted excerpt.

- [ ] **Step 3: Apply the fix to `_candidate_card`**

Edit `app/bot/v4_notifications.py:60-82`. Replace the body of `_candidate_card` with:

```python
def _candidate_card(
    candidate: OpportunityCandidate,
    rank: int,
    brief: CandidateBrief | None,
    score: float | None,
) -> str:
    title = md_escape(candidate.problem_statement or "")
    score_str = md_escape(str(int(score + 0.5)) if score is not None else "—")
    lc = lifecycle_arrow(candidate.lifecycle_state)
    lc_str = md_escape(lc) if lc else ""

    parts = [f"\\#{rank} — *{title}* — Score: *{score_str}*"]
    if lc_str:
        parts[0] += f"  {lc_str}"

    if candidate.audience:
        parts.append(f"Audience: {md_escape(candidate.audience)}")

    return "\n".join(parts)
```

Notes:
- The `brief` parameter is kept in the signature — `build_digest_buttons` still uses it to populate `brief_id` in callback data, and `build_digest_message` still passes it.
- The `truncate` import in `app/bot/v4_notifications.py:13` is still used by `_build_alert_text` (line 266). Leave the import line alone.

- [ ] **Step 4: Re-run the new tests + the existing notification tests**

Ask the user to run:

```
! uv run pytest tests/bot/test_v4_notifications.py -v
```

Expected: all green, including `test_digest_renders_top_3` and `test_digest_includes_lifecycle_arrow` (regression).

- [ ] **Step 5: Commit**

```bash
git add app/bot/v4_notifications.py tests/bot/test_v4_notifications.py
git commit -m "fix(bot): render full problem statement and drop excerpt in digest cards"
```

---

## Task 2 — Extract `_render_opportunity_card` helper (pure refactor)

This task has no new test of its own — the existing `cmd_opportunity` tests (`test_opportunity_unknown_id_returns_friendly_error`, `test_opportunity_below_gate_shows_warning` in `tests/bot/test_v4_handlers.py`) serve as the regression guard. They must still pass after the extraction.

**Files:**
- Modify: `app/bot/v4_handlers.py:142-246` (`cmd_opportunity`)

- [ ] **Step 1: Run the existing `cmd_opportunity` tests first as a baseline**

Ask the user to run:

```
! uv run pytest tests/bot/test_v4_handlers.py -v
```

Expected: all PASS. Record the result so you can confirm the refactor preserves behavior.

- [ ] **Step 2: Add the new `_render_opportunity_card` helper above `cmd_opportunity`**

Open `app/bot/v4_handlers.py`. Just above the existing `async def cmd_opportunity(...)` (line 142), insert:

```python
async def _render_opportunity_card(
    session,
    candidate_id: int,
    settings,
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Render the full scorecard for a single candidate.

    Returns (text, markup) or None if the candidate does not exist.
    Shared by /opportunity command and the 'view:' inline-button callback.
    """
    c_result = await session.execute(
        select(OpportunityCandidate).where(OpportunityCandidate.id == candidate_id)
    )
    c = c_result.scalars().first()
    if c is None:
        return None

    score = await _fetch_latest_score(session, c.id)
    brief = await _fetch_latest_brief(session, c.id)

    pp_result = await session.execute(
        select(PainPoint, SourceItem)
        .join(SourceItem, PainPoint.source_item_id == SourceItem.id)
        .where(PainPoint.candidate_id == c.id)
        .order_by(PainPoint.extracted_at.desc())
        .limit(5)
    )
    evidence_rows = pp_result.all()

    val_result = await session.execute(
        select(CandidateValidation)
        .where(CandidateValidation.candidate_id == c.id)
        .where(CandidateValidation.signal_type == "composite")
        .order_by(CandidateValidation.validated_at.desc())
        .limit(1)
    )
    validation = val_result.scalars().first()

    lines: list[str] = []

    if c.is_archived:
        lines.append("⚠️ This opportunity has been archived\\.")
        lines.append("")

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
        lines.append(score_breakdown_block(score.score_breakdown_json, score.score_total))
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
    return text, markup
```

- [ ] **Step 3: Replace `cmd_opportunity` body with a thin wrapper**

In the same file, replace the entire existing `cmd_opportunity` function (currently lines 142–246) with:

```python
async def cmd_opportunity(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/opportunity <id> — Full scorecard for a candidate."""
    if not update.effective_message:
        return
    _log.info(
        "cmd_opportunity",
        chat_id=update.effective_chat.id if update.effective_chat else None,
        candidate_id=ctx.args[0] if ctx.args else None,
    )

    if not ctx.args:
        await update.effective_message.reply_text(
            "Usage: /opportunity \\<id\\>", parse_mode="MarkdownV2"
        )
        return

    try:
        candidate_id = int(ctx.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "Please provide a numeric candidate id\\.", parse_mode="MarkdownV2"
        )
        return

    settings = get_settings()

    async with get_session() as session:
        rendered = await _render_opportunity_card(session, candidate_id, settings)

    if rendered is None:
        await update.effective_message.reply_text(
            "Candidate not found\\.", parse_mode="MarkdownV2"
        )
        return

    text, markup = rendered
    await update.effective_message.reply_text(
        text, parse_mode="MarkdownV2", reply_markup=markup
    )
```

- [ ] **Step 4: Re-run the `cmd_opportunity` tests to confirm zero behavior change**

Ask the user to run:

```
! uv run pytest tests/bot/test_v4_handlers.py -v
```

Expected: all tests still PASS. If any fail, the extraction introduced a regression — fix before continuing.

- [ ] **Step 5: Commit**

```bash
git add app/bot/v4_handlers.py
git commit -m "refactor(bot): extract _render_opportunity_card helper from cmd_opportunity"
```

---

## Task 3 — `cmd_view_callback` in new `app/bot/details.py`

**Files:**
- Create: `app/bot/details.py`
- Test: `tests/bot/test_details_callback.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/bot/test_details_callback.py`:

```python
"""Tests for app/bot/details.py — the 'view:' inline-button callback."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.details import cmd_view_callback
from app.models import OpportunityCandidate


def _make_callback_update(data: str, user_id: int = 42, chat_id: int = 99) -> MagicMock:
    """Mirror the test helper from tests/bot/test_feedback.py for consistency."""
    update = MagicMock()
    query = AsyncMock()
    query.data = data
    query.from_user = MagicMock(id=user_id)
    query.message = MagicMock()
    query.message.chat_id = chat_id
    query.message.reply_text = AsyncMock()
    query.answer = AsyncMock()
    update.callback_query = query
    update.effective_chat = MagicMock(id=chat_id)
    return update


async def test_view_callback_replies_with_opportunity_card(
    session: AsyncSession, monkeypatch
) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    c = OpportunityCandidate(
        problem_statement="A specific developer pain", specificity=3
    )
    session.add(c)
    await session.commit()

    update = _make_callback_update(f"view:{c.id}:none")
    ctx = MagicMock()

    with patch("app.bot.details.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)
        await cmd_view_callback(update, ctx)

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.message.reply_text.assert_awaited_once()
    sent_text = update.callback_query.message.reply_text.call_args.args[0]
    assert "A specific developer pain" in sent_text


async def test_view_callback_unknown_candidate(
    session: AsyncSession, monkeypatch
) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    update = _make_callback_update("view:99999:none")
    ctx = MagicMock()

    with patch("app.bot.details.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)
        await cmd_view_callback(update, ctx)

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.message.reply_text.assert_awaited_once()
    sent_text = update.callback_query.message.reply_text.call_args.args[0]
    assert "not found" in sent_text.lower()


async def test_view_callback_malformed_data_does_not_reply() -> None:
    update = _make_callback_update("view:")
    ctx = MagicMock()

    await cmd_view_callback(update, ctx)

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.message.reply_text.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Ask the user to run:

```
! uv run pytest tests/bot/test_details_callback.py -v
```

Expected: all FAIL with `ModuleNotFoundError: No module named 'app.bot.details'`.

- [ ] **Step 3: Create `app/bot/details.py`**

Create the file with the following contents:

```python
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
```

- [ ] **Step 4: Re-run the tests to verify they pass**

Ask the user to run:

```
! uv run pytest tests/bot/test_details_callback.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/bot/details.py tests/bot/test_details_callback.py
git commit -m "feat(bot): add 'view:' callback handler for the 📄 details button"
```

---

## Task 4 — Register the `^view:` handler + regression guard

**Files:**
- Modify: `app/bot/handlers.py:83-103` (`register_command_handlers`)
- Test: `tests/bot/test_handlers_registration.py` (create)

- [ ] **Step 1: Write the failing regression test**

Create `tests/bot/test_handlers_registration.py`:

```python
"""Regression guards for bot handler registration.

The 'view:' callback handler was missing originally — that bug silently
broke every 📄 details button. This test makes sure it stays wired.
"""
from __future__ import annotations

from unittest.mock import patch

from telegram.ext import Application, CallbackQueryHandler

from app.bot.handlers import register_command_handlers


def _matches(handler: CallbackQueryHandler, sample: str) -> bool:
    pattern = handler.pattern
    if pattern is None:
        return False
    return pattern.match(sample) is not None


def test_view_callback_handler_is_registered() -> None:
    # Application.builder().token(...).build() requires a non-empty token
    # but does not contact Telegram, so a dummy string is fine.
    app = Application.builder().token("0:dummy").build()
    register_command_handlers(app)

    callback_handlers = [
        h
        for group in app.handlers.values()
        for h in group
        if isinstance(h, CallbackQueryHandler)
    ]

    assert any(_matches(h, "view:42:none") for h in callback_handlers), (
        "Expected a CallbackQueryHandler whose pattern matches 'view:...'"
    )
    # Sanity: existing feedback handler must remain registered too.
    assert any(_matches(h, "fb:up:1:none") for h in callback_handlers)
```

- [ ] **Step 2: Run the test to verify it fails**

Ask the user to run:

```
! uv run pytest tests/bot/test_handlers_registration.py -v
```

Expected: FAIL with the "Expected a CallbackQueryHandler whose pattern matches 'view:...'" assertion.

- [ ] **Step 3: Register the handler**

Edit `app/bot/handlers.py`. In `register_command_handlers` (around lines 83–103), modify the local imports and add the new registration line. Replace:

```python
def register_command_handlers(application: Application) -> None:
    from app.bot.v4_handlers import (
        cmd_categories,
        cmd_category,
        cmd_emerging,
        cmd_opportunities,
        cmd_opportunity,
    )
    from app.bot.feedback import cmd_feedback_callback
    from telegram.ext import CallbackQueryHandler

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CommandHandler("sources", sources_handler))
    application.add_handler(CommandHandler("opportunities", cmd_opportunities))
    application.add_handler(CommandHandler("opportunity", cmd_opportunity))
    application.add_handler(CommandHandler("categories", cmd_categories))
    application.add_handler(CommandHandler("category", cmd_category))
    application.add_handler(CommandHandler("emerging", cmd_emerging))
    application.add_handler(CallbackQueryHandler(cmd_feedback_callback, pattern=r"^fb:"))
```

with:

```python
def register_command_handlers(application: Application) -> None:
    from app.bot.v4_handlers import (
        cmd_categories,
        cmd_category,
        cmd_emerging,
        cmd_opportunities,
        cmd_opportunity,
    )
    from app.bot.feedback import cmd_feedback_callback
    from app.bot.details import cmd_view_callback
    from telegram.ext import CallbackQueryHandler

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CommandHandler("sources", sources_handler))
    application.add_handler(CommandHandler("opportunities", cmd_opportunities))
    application.add_handler(CommandHandler("opportunity", cmd_opportunity))
    application.add_handler(CommandHandler("categories", cmd_categories))
    application.add_handler(CommandHandler("category", cmd_category))
    application.add_handler(CommandHandler("emerging", cmd_emerging))
    application.add_handler(CallbackQueryHandler(cmd_feedback_callback, pattern=r"^fb:"))
    application.add_handler(CallbackQueryHandler(cmd_view_callback, pattern=r"^view:"))
```

- [ ] **Step 4: Re-run the regression test + full bot test suite**

Ask the user to run:

```
! uv run pytest tests/bot/ -v
```

Expected: every test PASS, including the new regression guard and every test from earlier tasks.

- [ ] **Step 5: Commit**

```bash
git add app/bot/handlers.py tests/bot/test_handlers_registration.py
git commit -m "feat(bot): register ^view: CallbackQueryHandler so 📄 details button works"
```

---

## Final verification

- [ ] **Step 1: Run the entire test suite**

Ask the user to run:

```
! uv run pytest -v
```

Expected: everything green. Any failure outside `tests/bot/` is unrelated; flag it but do not attempt fixes here.

- [ ] **Step 2: Manual smoke check (optional, requires a live bot)**

If a staging/dev Telegram chat is available, trigger `run_digest_job` or wait for the scheduled digest. Verify:
- Card #1 ends with a full sentence (no orphaned mid-word fragment).
- Tapping 📄 produces a reply message containing `*<problem statement>*`, the score breakdown code-fence, and an Evidence section.
- Tapping 📄 on `/opportunities`, `/emerging`, `/category <slug>`, and lifecycle alerts also works (all share the same `view:` callback prefix).
