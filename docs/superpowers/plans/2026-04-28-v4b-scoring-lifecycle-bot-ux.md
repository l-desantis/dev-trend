# Plan — v4.B: Scoring, Lifecycle, Bot UX & Feedback

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.
>
> **Environment note:** Same Windows/WSL2 + `uv` constraints as Plan A. Pass commands to the user; no direct git commits.

**Goal:** Surface v4 candidates to users via Telegram with scoring, lifecycle tracking, and 👍/👎 feedback. End state: daily digest at 08:00 UTC, lifecycle transition alerts, all six v4 bot commands working, feedback persisted.

**Architecture:** Add stages 6–9 (validation, scoring, lifecycle, brief generation) on top of Plan A's pipeline. Wire daily scoring cron to chain into lifecycle alerts (same pattern as v3's scoring → spike-alert chain). Replace v3 bot handlers with v4 commands. Add Telegram inline-button feedback flow.

**Tech Stack:** Same as Plan A. Adds `httpx` for GitHub repo-search calls (already a dependency). Plan B does not introduce new packages.

**Spec reference:** `docs/superpowers/specs/2026-04-28-opportunity-discovery-pivot-design.md`

**Depends on:** Plan A complete and merged. Plan A's `OpportunityCandidate` rows and `PainPoint` evidence must be flowing.

---

## Context

After Plan A, the DB accumulates candidates each day but users see none of it. Plan B closes the loop end-to-end: candidates get validated against GitHub/Show HN, scored across five dimensions, lifecycle-tracked, surfaced in Telegram, and capture user feedback.

The two operational additions worth flagging up front:

- **Scoring → lifecycle alert chain.** v3's scoring job awaited the spike alert before returning (ADR-006). v4 follows the same pattern: the scoring coroutine awaits `emit_lifecycle_alerts()` before completing. No race between writing `CandidateScoreHistory` and reading it.
- **Brief generation runs at digest time, not scoring time.** Stage 9 only runs against the top-N candidates that the digest is about to push. The cost is bounded (≤ 3 LLM calls/day for top-3) regardless of how many candidates exist.

---

## File-Level Plan

**New files:**
- `app/pipeline/validation.py` — Stage 6
- `app/scoring/__init__.py`
- `app/scoring/dimensions.py` — raw computations per dimension
- `app/scoring/normalize.py` — percentile rank over candidate population
- `app/scoring/candidate_scorer.py` — composite scorer + persistence
- `app/pipeline/lifecycle.py` — state derivation + transition detection
- `app/pipeline/brief_generation.py` — Stage 9
- `app/bot/v4_handlers.py` — `/opportunities`, `/opportunity`, `/categories`, `/category`, `/emerging`
- `app/bot/v4_notifications.py` — digest + lifecycle-alert builders
- `app/bot/feedback.py` — callback query handler for 👍/👎
- Tests under `tests/scoring/`, `tests/pipeline/test_validation.py`, `tests/pipeline/test_lifecycle.py`, `tests/pipeline/test_brief_generation.py`, `tests/bot/test_v4_handlers.py`, `tests/bot/test_feedback.py`

**Modified:**
- `app/bot/handlers.py` — wire in v4 handlers (the file may eventually be split; for Plan B keep it as a thin re-export layer)
- `app/bot/formatter.py` — add `lifecycle_arrow`, `score_breakdown_block`
- `app/bot/middleware.py` — extend allowlist to `CallbackQuery` updates (currently only checks `Message`)
- `app/ingestion/scheduler.py` — add daily scoring + digest crons
- `app/main.py` lifespan — register feedback callback handler with the bot dispatcher
- `app/config.py` + `.env.example` — add new keys (B-09)

**Removed:** none (Plan A removed the v3 surface; Plan B only adds).

---

## Tasks

### B-01 — Stage 6: validation.py

**Files:** `app/pipeline/validation.py`, `tests/pipeline/test_validation.py`

```python
async def run_validation(
    session: AsyncSession,
    github_client: httpx.AsyncClient,
    *,
    only_active: bool = True,
    refresh_age_days: int = 7,
) -> ValidationReport:
    """Stage 6.

    For each OpportunityCandidate (is_archived=False; specificity > 0):
        if exists CandidateValidation in last refresh_age_days: skip
        else:
            keywords = extract_keywords(candidate.problem_statement, candidate.audience)
            github = await search_github_repos(github_client, keywords)
            show_hn = count_show_hn_matches(session, candidate)
            insert CandidateValidation(...)
    """
```

**`extract_keywords`** is rule-based (no LLM): take noun phrases from `problem_statement` + `audience`, drop stopwords, take top 5 by length. The keyword string fed to GitHub is `'+'.join(top_keywords)`. Quality-of-keyword matters less than recency: a noisy but consistent keyword set produces consistent star-delta-over-time signal, which is what the Validation dimension actually needs.

**`search_github_repos`:**
- Endpoint: `GET https://api.github.com/search/repositories?q={keywords}+in:name,description,readme&sort=stars&per_page=30`
- Returns: `repo_count` (capped at 30; GitHub's response includes total_count which we use directly for the dimension), `top_repos_json` (top 5 by stars: name, stars, url, language).
- `star_delta_30d`: for each of the top 5, hit `/repos/{owner}/{name}/stargazers` (paginated; expensive). **Phase 1 simplification:** approximate as `stars - stars_30d_ago` using the previous `CandidateValidation` row for the same candidate. First-ever validation has `star_delta_30d=0`.
- Reuse `_request_with_retry` from existing connectors for rate-limit / 403 handling.

**`count_show_hn_matches`:**
- SQL: `SELECT COUNT(*) FROM source_items WHERE role='validation' AND source_type='hn' AND ingested_at >= now-30d AND lower(title) LIKE ANY(:keyword_patterns)`.
- `top_show_hn_json`: top 5 by HN points (stored in `metadata_json['points']`).

**Refresh policy:** re-validate at most weekly per candidate (default `refresh_age_days=7`). Daily scoring uses the most-recent snapshot — fresh enough for slow-moving GitHub-stars signal.

**Tests:**
- `test_validation_skips_recent_snapshot` — pre-seed CandidateValidation 2 days old; assert no new row.
- `test_validation_creates_snapshot_when_stale` — pre-seed snapshot 8 days old; assert new row.
- `test_validation_skips_archived` — archived candidate is skipped.
- `test_validation_handles_zero_repo_match` — mock GitHub to return total_count=0; assert snapshot row with repo_count=0.
- `test_extract_keywords_strips_stopwords` — input `"a habit tracker for ADHD adults"` → keywords without `"a"`, `"for"`.
- `test_count_show_hn_matches` — seed 3 Show HN items with matching titles, 1 non-matching; assert 3.

**Suggested commit:** `feat(pipeline): stage 6 — validation via GitHub + Show HN`

---

### B-02 — `app/scoring/dimensions.py`

**Files:** `app/scoring/dimensions.py`, `tests/scoring/test_dimensions.py`

Five raw-value functions; all return floats in [0, 100] **except** before percentile normalisation, where they return whatever raw range the metric naturally lives in. Percentile normalisation in B-03 maps to 0–100.

```python
def frequency_raw(session, candidate_id, *, window_days=30) -> float:
    """Count of PainPoints attached and extracted within window_days."""

def momentum_raw(session, candidate_id, *, window_days=7) -> float:
    """7-day rolling slope of daily PainPoint attachment count.
    Uses linear_regression_slope from app/features/trend_features.py."""

def source_diversity_raw(session, candidate_id, *, window_days=30) -> float:
    """Count distinct (source_type, sub_or_app) pairs across attached PainPoints."""

def validation_curve(repo_count: int, max_stars: int) -> float:
    """Spec §5.2 — non-monotonic.
    Returns 0–100 directly (no percentile normalisation for this dim)."""
    if repo_count == 0: return 30.0
    if repo_count <= 5  and max_stars <= 5_000:  return 90.0
    if repo_count <= 20 and max_stars <= 20_000: return 70.0
    return 30.0

def specificity_raw(candidate) -> float:
    """OpportunityCandidate.specificity is already 1–5; map to 20–100 linearly."""
    return float(candidate.specificity * 20)
```

`source_diversity_raw` needs the sub/app embedded somewhere accessible. For Reddit, `source_item.metadata_json['subreddit']`; for HN, the role tag covers it (Ask HN vs comments aren't differentiated in this dim — both count as "hn"); for Play Store, `metadata_json['app_id']`. Implement a small helper `_source_bucket(source_item) -> str` returning `f"{source_type}:{sub_or_app}"` or just `source_type` when no sub-bucket exists.

**Tests:**
- `test_frequency_raw_counts_within_window` — pre-seed 5 PainPoints (3 in window, 2 outside); assert 3.
- `test_momentum_raw_positive_for_growing_attachment` — seed PainPoints across 7 days with rising daily counts; assert slope > 0.
- `test_source_diversity_distinct_buckets` — seed PainPoints from r/startups, r/SideProject, HN; assert 3.
- `test_validation_curve_each_band` — params for each spec band; assert 30/90/70/30.
- `test_specificity_raw_mapping` — specificity=3 → 60.0.

**Suggested commit:** `feat(scoring): per-dimension raw computations`

---

### B-03 — Percentile normalisation

**Files:** `app/scoring/normalize.py`, `tests/scoring/test_normalize.py`

Reuses `percentile_rank` from `app/features/trend_features.py` (the v3 helper). New module wraps it for the candidate population:

```python
def normalize_dimension_across_candidates(
    raw_values: dict[int, float],   # candidate_id → raw value
) -> dict[int, float]:
    """Returns candidate_id → percentile-rank score in [0, 100].
    Uses percentile_rank() over the population of raw_values."""

def normalize_with_neutral_fallback(
    raw_values: dict[int, float],
    *,
    min_population: int = 5,
    fallback: float = 50.0,
) -> dict[int, float]:
    """If population < min_population, return fallback for every candidate.
    Mirrors the M3 ADR-004 'sparse history → neutral 50' pattern."""
```

Validation dimension is NOT percentile-normalised — `validation_curve()` already returns 0–100 directly. The composite scorer skips normalisation for that dim.

**Tests:**
- `test_normalize_basic` — 5 raws spread evenly; expected ranks roughly 10/30/50/70/90.
- `test_normalize_neutral_fallback_applies` — 3 raws with `min_population=5`; assert all 50.0.
- `test_normalize_handles_ties` — 5 candidates all with raw=10; all get the same rank.

**Suggested commit:** `feat(scoring): percentile normalisation across candidate population`

---

### B-04 — Composite scorer

**Files:** `app/scoring/candidate_scorer.py`, `tests/scoring/test_candidate_scorer.py`

```python
WEIGHTS = {"frequency": 0.25, "momentum": 0.30, "source_diversity": 0.15,
           "validation": 0.20, "specificity": 0.10}

async def score_all_candidates(
    session: AsyncSession,
    *,
    as_of: datetime,
) -> list[CandidateScoreHistory]:
    """For every active (not-archived) candidate above the specificity gate:
        compute raw values for 4 dimensions
        normalise (percentile rank) for frequency, momentum, source_diversity
        compute validation_curve from the latest CandidateValidation
        compute specificity_raw (deterministic, already 0–100)
        composite = sum(dim * weight)
        insert CandidateScoreHistory(score_total, score_breakdown_json, scored_at=as_of)

    Idempotent for the same `as_of` date: existing rows for that date are
    DELETED first (mirrors v3 score_all_niches pattern)."""
```

`score_breakdown_json` shape:
```json
{"frequency": {"raw": 12, "score": 65}, "momentum": {"raw": 0.4, "score": 80},
 "source_diversity": {"raw": 4, "score": 70}, "validation": 90, "specificity": 80,
 "weights": {"frequency": 0.25, ...}}
```

Storing the raw value alongside the percentile-normalised score lets `/opportunity <id>` show both ("12 pain points → 65th percentile") which is the interpretability win the spec calls for.

The specificity gate is enforced at scoring time: candidates with `specificity <= settings.specificity_gate` (default 2) are **skipped** entirely — no row in `CandidateScoreHistory`. They still exist in the DB for the weekly re-cluster relabel pass.

**Tests:**
- `test_score_all_candidates_writes_history` — seed 3 candidates with PainPoints; run; assert 3 rows in `CandidateScoreHistory`.
- `test_score_idempotent_same_day` — run twice for same `as_of`; assert still 3 rows (not 6).
- `test_score_skips_below_specificity_gate` — specificity=2 candidate; assert no row written.
- `test_score_breakdown_includes_raw_and_normalised`
- `test_score_total_matches_weighted_sum` — manually compute expected total from breakdown; assert match.

**Suggested commit:** `feat(scoring): composite candidate scorer + history persistence`

---

### B-05 — Stage 8: lifecycle.py

**Files:** `app/pipeline/lifecycle.py`, `tests/pipeline/test_lifecycle.py`

```python
def derive_lifecycle_state(
    candidate: OpportunityCandidate,
    history: list[CandidateScoreHistory],   # last 14 days, oldest-first
) -> str | None:
    """Returns one of: 'emerging', 'hot', 'saturated', 'dormant', None.
    Spec §5.3 rules."""

async def update_lifecycle_states_and_emit_transitions(
    session: AsyncSession,
    *,
    as_of: datetime,
) -> list[LifecycleTransition]:
    """For each scored candidate today:
        old = OpportunityCandidate.lifecycle_state (last persisted)
        new = derive_lifecycle_state(candidate, history)
        if new != old:
            update OpportunityCandidate.lifecycle_state = new
            collect LifecycleTransition(candidate_id, old, new, score_total)
    Returns the collected transitions, sorted by score_total DESC."""
```

`LifecycleTransition` is a Pydantic dataclass: `candidate_id, old_state, new_state, score_total, problem_statement` (last field copied for downstream notification rendering — saves an extra DB hit).

**Persistence: `LifecycleEvent` table.** Each transition is also written to a new ORM table `LifecycleEvent(id, candidate_id, old_state, new_state, recorded_at, score_total, was_alerted)` (add to `app/models.py` in this task). The `was_alerted` bool is set after `emit_lifecycle_alerts()` returns: alerts that were actually pushed get `was_alerted=True`; capped overflow gets `False`. The digest job (B-08) reads recent unalerted rows to compute the overflow-note count. Pruning: `LifecycleEvent` rows older than 30 days are removed by the weekly pruning job (extension covered in Plan C).

The momentum/frequency thresholds in `derive_lifecycle_state` use the *normalised* scores from the latest history row (read from `score_breakdown_json`), not raw values. Reason: thresholds are stable across the candidate population because the inputs are percentile-normalised.

```python
def derive_lifecycle_state(candidate, history):
    if not history: return None
    latest = history[-1]
    bd = latest.score_breakdown_json
    momentum, frequency = bd["momentum"]["score"], bd["frequency"]["score"]

    age_days = (latest.scored_at - candidate.created_at).days
    last_pp_age = (latest.scored_at - candidate.last_evidence_at).days

    if last_pp_age >= 14: return "dormant"
    if momentum >= 60 and frequency < 30 and age_days < 14: return "emerging"
    if momentum >= 60 and frequency >= 30: return "hot"
    if frequency >= 70 and momentum < 30: return "saturated"
    return None
```

**Tests:**
- `test_derive_emerging` — momentum=70, frequency=20, age=5d → 'emerging'.
- `test_derive_hot` — momentum=70, frequency=40 → 'hot'.
- `test_derive_saturated` — momentum=20, frequency=80 → 'saturated'.
- `test_derive_dormant_overrides_other_signals` — last_evidence_at 20 days ago; momentum still high → 'dormant'.
- `test_derive_none_when_no_match`
- `test_update_emits_transition_only_on_change` — pre-seed candidate with `lifecycle_state='emerging'`; new derived state also 'emerging'; assert empty transitions list AND no new LifecycleEvent row.
- `test_update_emits_transition_on_change` — pre-seed 'emerging' → derived 'hot' → assert one transition emitted AND one LifecycleEvent row written with `was_alerted=False` (alerter sets the flag later).
- `test_update_persists_new_state`
- `test_lifecycle_event_was_alerted_flag` — after `emit_lifecycle_alerts` (B-09) runs, alerted rows have `was_alerted=True`; over-cap rows stay False.

**Suggested commit:** `feat(pipeline): stage 8 — lifecycle state + transition detection`

---

### B-06 — Stage 9: brief_generation.py

**Files:** `app/pipeline/brief_generation.py`, `tests/pipeline/test_brief_generation.py`

```python
async def generate_briefs_for(
    session: AsyncSession,
    llm: LLMAdapter,
    candidates: list[OpportunityCandidate],
    *,
    timeout_s: float = 90.0,
) -> list[CandidateBrief]:
    """For each candidate, call llm.generate_brief() with a timeout.
    Persist CandidateBrief with denormalised evidence_json snapshot.
    Returns inserted briefs in order. Failures are logged and the candidate
    is skipped (no partial brief)."""
```

`evidence_json` is the denormalised snapshot — list of up to 5 PainPoints, each with `{problem_text, audience, source_type, source_url, excerpt, extracted_at}`. Same pattern as v3's `OpportunityBrief.evidence_json`.

`generate_brief` reuses the existing `LLMAdapter.generate_brief` — keep its signature compatible with v3 to avoid breaking the adapter contract. Pass `(candidate, evidence)` and the implementation builds the prompt internally.

**Idempotency:** check if a `CandidateBrief` exists for `(candidate_id, generated_at::date)` before creating a new one. Same-day re-run is a no-op.

**Tests:**
- `test_generate_brief_persists` — using MockLLMAdapter; pre-seed candidate + 3 PainPoints; run; assert one CandidateBrief with non-empty summary and evidence_json.
- `test_generate_brief_timeout_skips_candidate` — patch `llm.generate_brief` to sleep 100s; assert candidate skipped, no brief inserted, logged warning.
- `test_generate_brief_idempotent_same_day` — running twice produces one brief.

**Suggested commit:** `feat(pipeline): stage 9 — brief generation for top-N candidates`

---

### B-07 — Daily scoring cron

**Files:** `app/ingestion/scheduler.py`, `app/config.py`, `tests/test_scheduler.py`

Add a daily cron `daily_scoring` that runs:
1. `run_validation()` — Stage 6
2. `score_all_candidates(as_of=today)` — writes `CandidateScoreHistory`
3. `update_lifecycle_states_and_emit_transitions()` — writes lifecycle, returns transitions
4. `await emit_lifecycle_alerts(transitions, bot)` — pushes Telegram alerts (chained, awaited; same pattern as v3 ADR-006)

Default cron: 04:00 UTC (after the daily pipeline at 03:30 from Plan A; gives 30 min for the pipeline to finish even on a slow day).

```python
scheduler.add_job(
    _scoring_job,
    CronTrigger(hour=settings.scoring_cron_hour),
    id="daily_scoring",
    max_instances=1, coalesce=True, misfire_grace_time=3600,
)
```

`max_instances=1` prevents overlap with the next day's run if it's running long.

New config:
```python
scoring_cron_hour: int = 4
digest_cron_hour: int = 8
```

**Tests:** `test_scheduler_registers_v4b_jobs` — assert `daily_scoring` and `daily_digest` jobs exist.

**Suggested commit:** `feat(scheduler): daily scoring + lifecycle cron`

---

### B-08 — Daily digest cron

**Files:** `app/ingestion/scheduler.py`, `app/bot/v4_notifications.py`, `tests/bot/test_v4_notifications.py`

Daily cron at 08:00 UTC. Flow:

```python
async def _digest_job(bot: Bot, llm: LLMAdapter) -> None:
    async with session_factory() as session:
        # Top 3 by today's score
        top = await fetch_top_candidates(session, limit=3,
                                         min_specificity=settings.specificity_gate + 1)
        # Just-in-time brief generation
        briefs = await generate_briefs_for(session, llm, top)
        # Render
        text = build_digest_message(top, briefs)
        markup = build_digest_buttons(top)
        for chat_id in settings.telegram_allowed_chat_ids:
            try:
                await bot.send_message(chat_id, text, parse_mode="MarkdownV2", reply_markup=markup)
            except TelegramError as e:
                log.warning("digest_send_failed", chat_id=chat_id, error=str(e))
```

`build_digest_message` returns MarkdownV2 with one block per candidate:

```
🚀 *DevTrend Daily Brief* — 28 Apr 2026

#1 — *Habit tracker for ADHD adults* — Score: *78*  🔥 Hot
"Repeated complaint across r/ADHD..."
Sources: r/ADHD, HN, 3 Play Store apps
Validation: 3 small repos, no major incumbent.

#2 — ...
```

`build_digest_buttons` returns an `InlineKeyboardMarkup` with one row per candidate: `[👍 useful] [👎 not useful] [📄 details]`. Callback data: `fb:up:42`, `fb:down:42`, `view:42`.

`fetch_top_candidates` excludes `is_archived`, requires `specificity > settings.specificity_gate`, orders by latest `CandidateScoreHistory.score_total` DESC.

**Overflow note rendering.** Append a footer line *"+N other transitions overnight, see /opportunities."* when more transitions occurred than alerts were sent. Source of truth: the `LifecycleEvent` log table introduced in B-05. Query: `SELECT COUNT(*) FROM lifecycle_events WHERE recorded_at >= now-24h AND was_alerted = False AND new_state IN ('emerging', 'hot', 'saturated')`.

**Tests (using a Telegram mock bot):**
- `test_digest_renders_top_3` — seed 5 candidates with descending scores; run; assert message contains exactly the top 3.
- `test_digest_skips_below_specificity_gate`
- `test_digest_continues_on_individual_chat_failure` — 2 chats, one raises; assert second still receives message.
- `test_digest_includes_lifecycle_arrow` — candidate with state='hot' → message contains `🔥 Hot`.

**Suggested commit:** `feat(bot): daily digest with top-3 candidates + inline buttons`

---

### B-09 — Lifecycle alerts

**Files:** `app/bot/v4_notifications.py`, `app/config.py`, `tests/bot/test_lifecycle_alerts.py`

```python
async def emit_lifecycle_alerts(
    transitions: list[LifecycleTransition],
    bot: Bot,
    settings: Settings,
) -> int:
    """Pushes up to settings.max_alerts_per_day transitions, sorted by score_total DESC.
    Over-cap transitions are logged silently and surfaced in next morning's digest as
    the 'overflow' note. Returns count of alerts sent."""
```

Message shape (one per alert):

```
🔥 *Hot opportunity*

*Habit tracker for ADHD adults*
Score: 78 — emerging → hot
6 new pain points this week from 3 distinct sources.

Sources: r/ADHD, HN, Play Store reviews
[👍 useful] [👎 not useful] [📄 details]
```

Lifecycle-arrow lookup at top of message: `dormant → ` ignored (we don't celebrate dormancy); `emerging`, `hot`, `saturated` get their own emoji + heading.

The "+N other transitions overnight" hint is appended to the next morning's digest by reading transitions not pushed today (logged with a `lifecycle_transition_overflow=true` flag). Implementation: a small `OverflowNote` table — *or* simpler — re-derive from the lifecycle column the morning the digest runs by counting `lifecycle_state` changes since 24h ago. **Decision: re-derive on demand; no new table.** Spec didn't ask for one.

New config:
```python
max_alerts_per_day: int = 3   # already declared in Plan A — confirmed in use here
```

**Tests:**
- `test_emit_lifecycle_alerts_caps_at_max` — 5 transitions, max=3; assert 3 sent.
- `test_emit_lifecycle_alerts_sorts_by_score`
- `test_emit_lifecycle_alerts_skips_dormant_to_emerging` *(or whatever directional rules we want — keep it simple: only push when new state ∈ {emerging, hot, saturated}; dormant transitions are silent).*
- `test_emit_lifecycle_alerts_continues_on_chat_failure`

**Suggested commit:** `feat(bot): lifecycle transition alerts with daily cap`

---

### B-10 — `lifecycle_arrow` formatter helper

**Files:** `app/bot/formatter.py`, `tests/bot/test_formatter.py`

```python
def lifecycle_arrow(state: str | None) -> str:
    return {
        "emerging":  "🌱 Emerging",
        "hot":       "🔥 Hot",
        "saturated": "🛑 Saturated",
        "dormant":   "💤 Dormant",
    }.get(state or "", "")

def score_breakdown_block(breakdown: dict) -> str:
    """MarkdownV2-safe rendering of score_breakdown_json for /opportunity <id>."""
```

`score_breakdown_block` produces a fixed-width-ish block:
```
Frequency       12 raw  ·  65/100
Momentum       0.4 raw  ·  80/100
Diversity        4 raw  ·  70/100
Validation             ·  90/100
Specificity      4 raw  ·  80/100
─────────────────────
Total                  ·  78/100
```

(MarkdownV2 doesn't have monospaced unless wrapped in ``` blocks; render this inside a ``` code fence.)

**Tests:** straight assertions on output strings.

**Suggested commit:** `feat(bot): formatter helpers for lifecycle + score breakdown`

---

### B-11 — `/opportunities`

**Files:** `app/bot/v4_handlers.py`, `tests/bot/test_v4_handlers.py`

```python
async def cmd_opportunities(update: Update, ctx) -> None:
    """Top N candidates by current score, MarkdownV2 list with inline buttons.
    Excludes is_archived and below-specificity-gate.
    N default 10, configurable via /opportunities 5."""
```

Renders the same per-candidate block as the digest but in a list (no per-message split — one message ≤ 4096 chars; truncation footer if needed via existing `truncate()` helper from M5). Each card has the same `[👍] [👎] [📄]` buttons.

**Tests:**
- `test_opportunities_returns_top_n` — seed 5 candidates; assert 5 rendered (or top N).
- `test_opportunities_argument_overrides_default` — `/opportunities 3` returns 3.
- `test_opportunities_excludes_archived`
- `test_opportunities_excludes_below_gate`
- `test_opportunities_handles_empty_db` — no candidates → "No opportunities yet — give the pipeline a few days to warm up."

**Suggested commit:** `feat(bot): /opportunities command`

---

### B-12 — `/opportunity <id>`

**Files:** `app/bot/v4_handlers.py`, `tests/bot/test_v4_handlers.py`

```python
async def cmd_opportunity(update: Update, ctx) -> None:
    """Full candidate scorecard. Args: candidate id (integer)."""
```

Renders:
- Header: problem_statement + lifecycle_arrow
- Audience and Why-now
- Latest brief summary if one exists (else 'Brief generates at digest time')
- `score_breakdown_block` from B-10
- Validation summary from latest CandidateValidation
- Top 5 evidence excerpts with source links (same pattern as v3 `/niche <slug>`)
- 4096-char truncation with "…see latest brief" footer
- 👍/👎 buttons

**Tests:**
- `test_opportunity_renders_full_view`
- `test_opportunity_unknown_id_returns_friendly_error` — id=9999 → "Candidate not found."
- `test_opportunity_archived_id_returns_archived_notice` — archived candidate is fetchable but message says "This opportunity has been archived (merged into #N)."
- `test_opportunity_truncates_long_evidence`

**Suggested commit:** `feat(bot): /opportunity <id> command`

---

### B-13 — `/categories`

**Files:** `app/bot/v4_handlers.py`, `tests/bot/test_v4_handlers.py`

Lists all 6 categories with active-candidate counts and lifecycle breakdowns:

```
📂 *Categories*

*Devtools* — 14 active · 3 hot · 2 emerging
*Productivity* — 9 active · 1 hot
*Wellness* — 7 active · 2 emerging
…
```

SQL: `SELECT category_id, lifecycle_state, COUNT(*) FROM opportunity_candidates WHERE is_archived=False AND specificity > :gate GROUP BY category_id, lifecycle_state`.

**Tests:**
- `test_categories_command_lists_all_with_counts`
- `test_categories_zero_active_renders_dash` — category with no candidates.

**Suggested commit:** `feat(bot): /categories overview`

---

### B-14 — `/category <slug>`

**Files:** `app/bot/v4_handlers.py`, `tests/bot/test_v4_handlers.py`

Top-N candidates in the named category. Slug must match `Category.slug` exactly. Render uses the same per-candidate block as `/opportunities`.

**Tests:**
- `test_category_filters_to_slug`
- `test_category_unknown_slug_lists_available` — unknown slug → "Unknown category. Available: wellness, finance, devtools, productivity, creative, gaming."

**Suggested commit:** `feat(bot): /category <slug> filter`

---

### B-15 — `/emerging`

**Files:** `app/bot/v4_handlers.py`, `tests/bot/test_v4_handlers.py`

Same render as `/opportunities` but filtered to `lifecycle_state='emerging'`. Sort by `created_at DESC` (newest emerging first — that's the "discovery feed" promise).

**Tests:**
- `test_emerging_filters_by_state`
- `test_emerging_sort_order`
- `test_emerging_empty_returns_friendly_message`

**Suggested commit:** `feat(bot): /emerging discovery feed`

---

### B-16 — Register handlers + command menu

**Files:** `app/bot/handlers.py`, `app/bot/bot.py`, `tests/bot/test_command_menu.py`

Wire the new handlers into the bot dispatcher in `app/bot/bot.py`. Update `set_my_commands` to advertise the v4 set:

```python
[
    BotCommand("start", "Welcome + commands"),
    BotCommand("help", "Show command list"),
    BotCommand("opportunities", "Top opportunities right now"),
    BotCommand("opportunity", "Full scorecard for an opportunity"),
    BotCommand("categories", "Overview by category"),
    BotCommand("category", "Filter by category slug"),
    BotCommand("emerging", "Newly-discovered opportunities"),
    BotCommand("sources", "Last ingestion status per source"),
]
```

Update `/help` text accordingly.

**Tests:** `test_set_my_commands_includes_v4_set` — assert each new command is in the registered list and `/briefing`, `/niches`, etc. are not.

**Suggested commit:** `feat(bot): register v4 command menu`

---

### B-17 — Feedback callback handler

**Files:** `app/bot/feedback.py`, `tests/bot/test_feedback.py`

```python
async def cmd_feedback_callback(update: Update, ctx) -> None:
    """Handles inline button callbacks: 'fb:up:<id>' and 'fb:down:<id>'.
    Inserts CandidateFeedback (uniqueness on (candidate_id, user_id, brief_id) flips on conflict)."""
```

Implementation:

```python
parts = update.callback_query.data.split(":")
assert parts[0] == "fb" and parts[1] in ("up", "down")
candidate_id = int(parts[2])

# Resolve brief_id from the message reply context — the message has an associated
# CandidateBrief.id stashed in callback_data of the [📄 details] button if available;
# else NULL.
brief_id = _extract_brief_id_from_message(update.callback_query.message)

stmt = sqlite_insert(CandidateFeedback).values(
    candidate_id=candidate_id,
    user_id=update.callback_query.from_user.id,
    chat_id=update.callback_query.message.chat_id,
    brief_id=brief_id,
    label=parts[1],
    created_at=now(),
).on_conflict_do_update(
    index_elements=["candidate_id", "user_id", "brief_id"],
    set_={"label": parts[1], "created_at": now()},
)
await session.execute(stmt)
await session.commit()

# UX: edit message keyboard to show "✓ marked X" instead of buttons
await update.callback_query.answer(text="Thanks — recorded.", show_alert=False)
await update.callback_query.edit_message_reply_markup(reply_markup=_replace_with_confirmation(parts[1]))
```

`_replace_with_confirmation('up')` returns a single-row keyboard with a disabled-looking `✓ Marked useful` button — visual confirmation, prevents double-clicks.

The brief_id-from-message extraction: we don't have a great hook for this. Practical approach — embed the brief_id in the `[📄 details]` callback_data as `view:<candidate_id>:<brief_id>`; when a feedback callback arrives, we read the message's reply_markup, find the `view:` entry, parse out brief_id. If the message has no `[📄 details]` button (e.g. older alerts), brief_id stays NULL.

**Tests:**
- `test_feedback_inserts_row` — fire callback `fb:up:42`; assert one row in CandidateFeedback.
- `test_feedback_flips_on_re_click` — fire `fb:up:42` then `fb:down:42` for same user; assert one row with label='down'.
- `test_feedback_per_user_independent` — user A 👍, user B 👎; assert two rows.
- `test_feedback_unknown_candidate_ignored_gracefully` — fire `fb:up:99999`; assert no crash, friendly callback answer.

**Suggested commit:** `feat(bot): inline feedback callback handler`

---

### B-18 — Allowlist middleware extension

**Files:** `app/bot/middleware.py`, `tests/bot/test_middleware.py`

Existing middleware checks `update.message.chat_id` against `TELEGRAM_ALLOWED_CHAT_IDS`. Extend it to also check `update.callback_query.message.chat_id` for callback updates (otherwise unauthorised chats could fire 👍/👎 if they ever obtained a button).

```python
def _chat_id_from_update(update: Update) -> int | None:
    if update.message: return update.message.chat_id
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message.chat_id
    return None
```

**Tests:**
- `test_middleware_blocks_callback_from_unallowed_chat`
- `test_middleware_allows_callback_from_allowed_chat`
- (existing message-allowlist tests still pass)

**Suggested commit:** `fix(bot): allowlist middleware also gates callback queries`

---

### B-19 — End-to-end push flow test

**Files:** `tests/bot/test_v4_e2e_push.py`

A single integration test that:
1. Sets up an in-memory DB with 3 active candidates of varying score/lifecycle.
2. Mocks the bot's `send_message` and tracks calls.
3. Runs `_scoring_job` (which runs validation, scoring, lifecycle, emit_alerts) — patched to use a mock LLM and a stubbed GitHub client.
4. Asserts: alerts pushed in score order, capped at `max_alerts_per_day`, each with the expected button structure.
5. Runs `_digest_job` — asserts top-3 message contains all three candidates and inline buttons.

**Suggested commit:** `test(bot): end-to-end push flow with mocks`

---

### B-20 — Specificity-gate enforcement audit

**Files:** any handler touching candidates

Sweep through B-04 (scorer), B-08 (digest), B-09 (alerts), B-11/13/14/15 (handlers), B-12 (`/opportunity <id>` — should it block? **decision: no**, allow direct ID lookup so the user can still see weak clusters if they manually request one). Confirm everywhere except `/opportunity <id>` filters out `specificity <= settings.specificity_gate`.

Add a config-dispatched test:
- `test_specificity_gate_consistently_applied` — seed a specificity=2 candidate; for each of `/opportunities`, `/categories`, `/category`, `/emerging`, digest, alerts: assert it's not surfaced.
- `test_opportunity_by_id_returns_below_gate_with_warning` — `/opportunity <id>` for a gated candidate returns the scorecard with a banner "⚠️ This opportunity is below the specificity threshold and may not be actionable yet."

**Suggested commit:** `test(scoring): specificity gate consistency audit`

---

### B-21 — Documentation update

**Files:** `README.md`, `KANBAN.md`, `docs/evaluation-plan.md`

`README.md`:
- Remove the Plan A "in progress" banner; replace with v4-current command summary.
- Document the LLM_PROVIDER / EMBEDDING_PROVIDER selection.
- Document the new daily timeline (03:30 pipeline, 04:00 scoring, 08:00 digest).
- Mention the 👍/👎 feedback collection — explain it's stored but not yet acted upon.

`KANBAN.md`: mark Plan A tasks done; expand v4.B and v4.C entries.

`docs/evaluation-plan.md`: add a v4-specific section:
- **Extraction precision** — manual review of 50 random PainPoints; target ≥80% are genuine unmet-need signals.
- **Cluster coherence** — for each candidate with ≥5 PainPoints, manually rate "do these 5 belong together?" 1–5; target average ≥4.
- **Specificity calibration** — sanity-check the LLM's specificity scores against your own 1–5 ratings on 20 candidates; target Spearman ≥0.6.
- **Lifecycle stability** — count candidates that bounce between states day-to-day; target <5%.

**Suggested commit:** `docs(v4): update README + KANBAN + evaluation-plan for Plan B`

---

## Definition of Done — Plan B

- [ ] Validation snapshots are written weekly per active candidate
- [ ] `CandidateScoreHistory` is populated daily for all above-gate candidates
- [ ] Lifecycle states transition correctly; alerts fire (capped) immediately after scoring
- [ ] Daily digest pushes top-3 with inline buttons at 08:00 UTC
- [ ] All five new bot commands (`/opportunities`, `/opportunity`, `/categories`, `/category`, `/emerging`) work with allowlist middleware
- [ ] 👍/👎 callbacks insert/flip `CandidateFeedback` rows
- [ ] Allowlist middleware gates callback queries
- [ ] Full test suite green: `uv run pytest`
- [ ] Specificity gate is consistently enforced everywhere except `/opportunity <id>` (which warns instead)
- [ ] README and evaluation-plan docs updated to reflect v4 reality

---

## Risks & Mitigations (Plan B specific)

| Risk | Mitigation |
|---|---|
| GitHub `/search/repositories` rate limit (10 req/min unauthenticated, 30/min authenticated) | Weekly per-candidate refresh keeps daily call count below limit; reuse `_request_with_retry`; pinning a `GITHUB_TOKEN` raises ceiling. |
| `star_delta_30d` approximation drifts | Acceptable for v4 — the dimension is non-monotonic and uses banded thresholds, not raw deltas. Real per-day star history can land in v4.5 if needed. |
| Scoring weights are calibration guesses | Documented in spec §5.1; revisit after ≥30 days of real data. Weights are config keys. |
| Lifecycle bounce (a candidate flipping daily between states) | Tracked as an evaluation criterion; if observed, add hysteresis (require state to hold for 2 consecutive runs before transitioning). Not in Plan B scope. |
| Telegram inline-button replies on messages older than 48h fail silently | Document; Plan B does not implement re-rendering. Stale-button handling is a Phase 2 concern. |
| `MAX_ALERTS_PER_DAY` cap silently drops noisy days | The "+N other transitions overnight" digest line surfaces the overflow. |

---

*End of Plan B.*
