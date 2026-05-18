# Telegram Digest Output — Readability & Details Handler

**Date:** 2026-05-18
**Status:** Draft (pending user approval)

## Problem

Two independent defects make the Telegram output of `run_digest_job` unusable:

1. **Mid-word truncation.** `_candidate_card` in `app/bot/v4_notifications.py` hard-slices the problem statement at 80 characters with `[:80]` (no ellipsis, no word boundary). The brief excerpt is similarly capped at 120 characters via `truncate(...)`. Both produce visibly broken text like `"...organizing decks, dis"` and `"resulting i…"`. The total message stays well under Telegram's 4096-character limit — these are self-inflicted per-field caps, not a platform constraint.

2. **"📄 details" button silently does nothing.** Every emitter (`build_digest_buttons`, `_candidate_inline_buttons`, `_build_alert_buttons`) produces buttons with `callback_data` of the form `view:<candidate_id>:<brief_id|none>`, but `register_command_handlers` in `app/bot/handlers.py` only registers a `CallbackQueryHandler` for `^fb:`. With no matching handler, python-telegram-bot ignores the callback and Telegram's loading spinner on the button never clears.

## Goals

- The daily digest reads cleanly: full problem statement, no orphaned word fragments.
- Tapping 📄 on **any** card (digest, opportunity list, category, emerging, lifecycle alert) shows the full opportunity scorecard.
- Reuse existing rendering logic from `cmd_opportunity` rather than building a parallel path.

## Non-Goals

- No refactor of the duplicated `_candidate_card` / `_render_candidate_card` pair.
- No changes to scoring, lifecycle classification, brief generation, or button-row layout.
- No new commands.
- No changes to the `view:<cid>:<brief_id>` callback-data contract — the `brief_id` segment remains in case future callers need it.

## Design

### Digest card format (`app/bot/v4_notifications.py::_candidate_card`)

- Drop the `[:80]` slice; render the full `candidate.problem_statement` (escaped).
- Remove the brief-excerpt block entirely. The 📄 button now provides the full brief on demand, making the inline excerpt redundant.
- `Audience:` line stays as-is.
- The `brief` parameter remains in the signature because `build_digest_buttons` still uses it to populate `brief_id` in callback data.

Size sanity: at `digest_top_n=3`, expected total is well under 2 KB (Telegram limit is 4096). No new per-message truncation is required at current top_n.

### Details callback handler

**New module:** `app/bot/details.py` — mirrors the per-feature layout already used by `feedback.py`.

**Public function:**

```python
async def cmd_view_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None: ...
```

**Behavior:**

1. `query = update.callback_query`; `await query.answer()` to immediately dismiss Telegram's loading spinner.
2. Parse `query.data` (`"view:<cid>:<brief_id|none>"`). On malformed input or non-int `cid`, log a structured warning and return (no user-visible reply).
3. Open a DB session and call a new helper `_render_opportunity_card(session, candidate_id, settings)` extracted from `cmd_opportunity` in `app/bot/v4_handlers.py`. The helper returns `(text, InlineKeyboardMarkup)` or `None` when the candidate is missing.
4. If `None`: `await query.message.reply_text("Candidate not found\\.", parse_mode="MarkdownV2")`.
5. Otherwise: `await query.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=markup)`. The reply is a **new** message under the digest; the original digest is left intact.
6. Emit `log.info("cb_view", chat_id=..., candidate_id=...)` for parity with command handlers.

**Renderer extraction (`app/bot/v4_handlers.py`):**

Pull the body of `cmd_opportunity` (lines ~162–246: DB queries + lines assembly + truncation + button construction) into a pure helper:

```python
async def _render_opportunity_card(
    session, candidate_id: int, settings: Settings
) -> tuple[str, InlineKeyboardMarkup] | None: ...
```

`cmd_opportunity` then becomes a thin wrapper that parses `ctx.args`, calls the helper, and dispatches the reply or the "not found" message. `cmd_view_callback` calls the same helper. Single source of truth for the scorecard.

**Handler registration (`app/bot/handlers.py::register_command_handlers`):**

Add one line below the existing feedback registration:

```python
from app.bot.details import cmd_view_callback
application.add_handler(CallbackQueryHandler(cmd_view_callback, pattern=r"^view:"))
```

**Allowlist:** the existing `register_allowlist` middleware filters by `effective_chat.id`. Callback queries carry `effective_chat`, so callbacks are already covered — no middleware change required.

## Error Handling

| Scenario | Behavior |
|---|---|
| Malformed `callback_data` (wrong segment count, non-int id) | `query.answer()`; `log.warning("cb_view_malformed", data=...)`; no reply. |
| Candidate archived between digest send and tap | Renderer emits the existing `⚠️ This opportunity has been archived` banner (already present in `cmd_opportunity`). Free reuse. |
| Candidate row deleted | Renderer returns `None` → reply `"Candidate not found\\."`. |
| User outside `telegram_allowed_chat_ids` taps a button | Blocked by `register_allowlist`; handler never runs. |
| Telegram API failure on `reply_text` | Exception bubbles to python-telegram-bot's error handler. No retry. |
| Stale `brief_id` in callback data (brief deleted) | Ignored — the renderer queries the latest brief by candidate_id via the existing `_fetch_latest_brief` helper. The `brief_id` segment is preserved in the contract but not consumed by the view handler. |

## Testing

**Extend `tests/bot/test_v4_notifications.py`:**

- `test_candidate_card_shows_full_problem_statement` — pass a 200-character statement; assert it appears verbatim (escaped) in `_candidate_card` output.
- `test_candidate_card_omits_brief_excerpt` — pass a candidate plus a brief with `summary` set; assert the summary text is **not** present in the card.

**New `tests/bot/test_details_callback.py`:**

- `test_view_callback_replies_with_opportunity_card` — happy path. Mock `update.callback_query` with `data="view:42:7"`, stub the session to return a candidate; assert `query.answer()` was awaited and `query.message.reply_text` was called with text containing the problem statement.
- `test_view_callback_unknown_candidate` — candidate id not in DB → reply contains `"Candidate not found"`.
- `test_view_callback_malformed_data` — `data="view:"` → `query.answer()` is awaited, `reply_text` is **not** called, warning is logged.
- `test_register_handlers_includes_view_pattern` — build the Application via `build_application()` and assert a `CallbackQueryHandler` whose pattern compiles to `^view:` is registered. Regression guard against the original bug recurring.

## Files Touched

| File | Change |
|---|---|
| `app/bot/v4_notifications.py` | Trim `_candidate_card`: remove `[:80]` slice, delete brief-excerpt block |
| `app/bot/v4_handlers.py` | Extract `_render_opportunity_card` helper from `cmd_opportunity`; `cmd_opportunity` becomes a thin wrapper |
| `app/bot/details.py` | **New** — `cmd_view_callback` |
| `app/bot/handlers.py` | Register `CallbackQueryHandler(cmd_view_callback, pattern=r"^view:")` |
| `tests/bot/test_v4_notifications.py` | +2 tests for digest formatting |
| `tests/bot/test_details_callback.py` | **New** — 4 tests |

## Rollout

Single PR. No data migration, no config flag. Pre-existing chat IDs continue to work because the `view:` callback contract is unchanged — buttons that were previously dead simply start working once the handler ships.
