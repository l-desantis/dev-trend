# Code Review — v4.B Implementation (2026-04-30)

> Review of plan `docs/superpowers/plans/2026-04-28-v4b-scoring-lifecycle-bot-ux.md` against the implementation merged through commit `6303dfa`.
>
> Use this as a punch list. Each item has file:line references, the problem, and the fix. Items are grouped by priority — work through 🔴 first.

---

## 🔴 Critical (silent data corruption / wrong attribution within ~2 weeks)

### C-1 — Lifecycle history slice is wrong

**File:** `app/pipeline/lifecycle.py:88-95`

```python
.order_by(CandidateScoreHistory.scored_at.asc())
.limit(14)
```

Returns the **first 14 score rows ever**, not the last 14 days. After day 14, `latest = history[-1]` is no longer today's row, so `age_days` and `last_pp_age` are computed against an old `scored_at`. State derivation goes progressively stale.

**Fix:** filter by date and order ASC, e.g.
```python
since = as_of - timedelta(days=14)
.where(CandidateScoreHistory.scored_at >= since)
.order_by(CandidateScoreHistory.scored_at.asc())
```
Or `.order_by(scored_at.desc()).limit(14)` then reverse the list.

**Test gap:** `tests/pipeline/test_lifecycle.py` never seeds >14 score rows. Add a test that seeds 20 daily rows, asserts `derive_lifecycle_state` reads from today's row, and that lifecycle continues to update on day 20.

---

### C-2 — `_extract_brief_id_from_message` returns the wrong brief in multi-card messages

**File:** `app/bot/feedback.py:21-38`

The function walks the entire `inline_keyboard` and returns the **first** `view:` callback it finds. In `/opportunities`, `/category`, `/emerging`, and the digest, the message contains one row of buttons per candidate. A 👍 click on candidate #3 will be attributed to candidate #1's `brief_id`. The unique constraint `(candidate_id, user_id, brief_id)` then silently misjoins feedback to the wrong brief.

**Fix (preferred):** embed `brief_id` directly in the `fb:` callback data — `fb:up:<cid>:<bid>` (use `none` sentinel when no brief yet). Update:
- `app/bot/v4_handlers.py:_candidate_inline_buttons`
- `app/bot/v4_notifications.py:build_digest_buttons`
- `app/bot/v4_notifications.py:_build_alert_buttons`
- `app/bot/feedback.py:cmd_feedback_callback` to parse 4 segments instead of 3

**Fix (alt):** scan only the keyboard row that contains the matching `fb:*:<candidate_id>` button.

**Test gap:** `tests/bot/test_feedback.py::_make_callback_update` always wires a single-row keyboard. Add a test where the keyboard has buttons for candidates #1 and #2 and the user clicks candidate #2's 👍 — assert the inserted `CandidateFeedback.brief_id` matches candidate #2's brief, not #1's.

---

## 🟠 Important

### I-3 — Score subqueries read peak score ever, not latest

**Files:**
- `app/bot/v4_handlers.py:86-92` (`cmd_opportunities`)
- `app/bot/v4_handlers.py:317-324` (`cmd_category`)
- `app/bot/v4_notifications.py:146-159` (`_fetch_scores_for`)

All three do:
```python
select(candidate_id, func.max(score_total)).group_by(candidate_id)
```
That returns the all-time max score per candidate. A candidate that peaked at 90 last week and is at 50 today will outrank a fresh 80. `fetch_top_candidates` correctly filters `scored_at >= today_start`, so the digest top-3 are picked by today but the score *displayed* can be the historical peak — internally inconsistent.

**Fix:** select the latest by `scored_at DESC` (window function or correlated subquery), or filter to `scored_at >= today_start` to match `fetch_top_candidates`.

---

### I-4 — Empty-string `new_state` written to `LifecycleEvent`

**File:** `app/pipeline/lifecycle.py:106-124`

When `derive_lifecycle_state()` returns `None` but `old_state` was non-null, the code stores `new_state=new_state or ""` — an empty string, not NULL. Pollutes the table and breaks any analytics filter on `new_state IN (...)`. The corresponding `LifecycleTransition.new_state` is also `""`, so `emit_lifecycle_alerts` correctly skips it, but the row is junk data.

**Fix:** make `LifecycleEvent.new_state` nullable in `app/models.py:224`, store `None`, and don't emit a `LifecycleTransition` for these.

---

### I-5 — `cmd_emerging` has no limit

**File:** `app/bot/v4_handlers.py:356-391`

The query has no `.limit(...)`. With many emerging candidates the message exceeds Telegram's 4096-char cap; `truncate()` clips the text but **doesn't drop the corresponding inline buttons**, leaving orphan rows or rejection by the Telegram API.

**Fix:** apply `_DEFAULT_TOP_N` like the other handlers.

---

### I-6 — Overflow count can overestimate

**File:** `app/bot/v4_notifications.py:135-143` (`_count_overflow_transitions`)

Counts every unalerted `LifecycleEvent` in 24h. If a candidate flips state twice in one cycle, both rows are counted. Also: if `_scoring_job` fails after writing lifecycle but before alerts, those rows are surfaced as "overflow" the next morning even though they weren't capped — they were never attempted.

**Fix:** `SELECT COUNT(DISTINCT candidate_id)` and/or filter to events where no later alerted row exists for the same `(candidate_id, new_state)`.

---

## 🟡 Minor / polish

### P-7 — Alert-mark could mark the wrong `LifecycleEvent`

**File:** `app/bot/v4_notifications.py:227-238`

Selects the most recent unalerted event matching `(candidate_id, new_state)`. If a candidate transitioned to the same `new_state` twice in one day, the wrong row is marked.

**Fix:** pass `LifecycleEvent.id` through `LifecycleTransition` and mark by id.

---

### P-8 — `extract_keywords` discards input order

**File:** `app/pipeline/validation.py:36-48`

Sorting purely by length means a longer near-stopword wins over a more meaningful first-position term.

**Fix:** combine length × position weighting, or just take the first 5 unique non-stopwords.

---

### P-9 — `count_show_hn_matches` LIMIT 20 caps the count

**File:** `app/pipeline/validation.py:112-118`

`count = len(items)` after `.limit(20)` undercounts in busy 30-day windows.

**Fix:** issue a separate `func.count()` query for the count and keep the LIMIT only for `top_show_hn`.

---

### P-10 — `score_breakdown_block` recomputes total

**File:** `app/bot/formatter.py:64-77`

Total is already stored on the row (`CandidateScoreHistory.score_total`). Recomputing duplicates work and drifts if weights change.

**Fix:** accept `score_total` as a parameter and only recompute as a fallback.

---

### P-11 — Per-candidate DELETE inside loop

**File:** `app/scoring/candidate_scorer.py:59-65`

One batch `DELETE WHERE candidate_id IN (...) AND scored_at BETWEEN ...` would be cleaner. Acceptable now, noticeable at scale.

---

### P-12 — Inline imports in scheduler / feedback

**Files:** `app/ingestion/scheduler.py:89-133`, `app/bot/feedback.py:67`

Many in-function imports. Move to module top unless avoiding a real circular import.

---

### P-13 — Empty-list message in `cmd_opportunities` not using `md_escape`

**File:** `app/bot/v4_handlers.py:104-107`

The "No opportunities yet — give the pipeline a few days to warm up." string has manually escaped periods. If anyone edits the wording without remembering MarkdownV2, the message will silently break.

**Fix:** use `md_escape(...)` on the human-readable text.

---

## 🔵 Test coverage gaps to add alongside fixes

- **C-1:** seed 20 daily score rows, assert lifecycle still updates correctly on day 20.
- **C-2:** multi-card keyboard test (two candidates, click #2's 👍, assert correct brief_id).
- **I-3:** seed candidate with peak score 90 yesterday and 50 today; assert `/opportunities` and digest both show 50, not 90.
- **I-4:** when `derive_lifecycle_state` returns `None` from a non-null state, assert no `LifecycleEvent` row is written (or NULL is stored).
- **e2e (`test_v4_e2e_push.py:135`)** — `assert isinstance(transitions, list)` is too weak. With 5 emerging candidates seeded, assert at least N transitions actually fire and that overflow rows have `was_alerted=False`.

---

## ✅ What's solid (no action needed)

- Schema migrations (B-00) match the plan; new columns/types correct.
- `validation.py` handles GitHub failures gracefully.
- `brief_generation.py` idempotency via `today_start` is correct (SQLite no-`::date` note honored).
- `feedback.py` correctly handles SQLite's "NULL is distinct in UNIQUE" by branching on `brief_id is None`.
- Allowlist middleware verification-only test (B-18) is the right call.
- Specificity-gate audit (B-20) covers every surface.
- Digest, alerts, and feedback all tolerate per-chat send failures.

---

## Suggested execution order

1. **C-1** + matching test
2. **C-2** + matching test
3. **I-3** + matching test
4. **I-4** + matching test
5. **I-5** (one-line fix) + **I-6** (query rewrite)
6. P-7 through P-13 as a single polish commit
7. Strengthen e2e assertions

Each fix is independently committable. C-1 and C-2 should be standalone commits because they're behavior changes that warrant focused review.
