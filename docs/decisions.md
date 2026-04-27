# Architecture Decision Records

> ADRs are written per decision as they happen.
> Format: context → decision → consequences.

---

## ADR-001 — Product name: DevTrend

**Date:** 2026-04-23
**Status:** Accepted

**Context:** The original repo was scaffolded under the name "devradar". The v2.0 project document renamed the product to "DevTrend" in prose but left "devradar" in the repo directory, database filename, and change log, creating a naming drift that would propagate into log output, env vars, and documentation.

**Decision:** Standardise on **DevTrend** everywhere. Repo application directory is `devtrend/`, database file is `devtrend.db`, and all references to "devradar" are scrubbed.

**Consequences:** Consistent naming in logs, config, and docs from day one. Any future rename costs a single find-replace.

---

## ADR-002 — Event-loop model: single asyncio loop

**Date:** 2026-04-23
**Status:** Accepted

**Context:** Phase 1 runs three async subsystems in one process: python-telegram-bot v20+ (async), APScheduler, and FastAPI. A common failure mode is mixing sync and async schedulers, leading to blocked event loops, missed jobs, or deadlocks under load.

**Decision:** Use a **single asyncio event loop** for the entire process:
- `AsyncIOScheduler` from APScheduler (not `BackgroundScheduler`)
- python-telegram-bot in its native async polling mode
- FastAPI via `uvicorn` with the same loop

Scheduler jobs are async coroutines. No `run_coroutine_threadsafe`, no threads.

**Consequences:** Startup and shutdown logic must be coordinated (FastAPI lifespan hook starts/stops the scheduler). All scheduled jobs must be non-blocking — CPU-heavy work must be wrapped in `asyncio.to_thread` if needed. In return: no thread-safety concerns, no hidden deadlocks.

---

## ADR-003 — Forecasting: rolling 7-day slope instead of Prophet

**Date:** 2026-04-23
**Status:** Accepted

**Context:** The original plan specified Facebook Prophet for the Growth dimension. Prophet requires weeks to months of periodic observations to produce meaningful forecasts. The system starts from zero history; seeding Prophet with synthetic data produces decorative, not predictive, output.

**Decision:** Phase 1 uses a **7-day rolling linear regression slope** on per-niche signal counts as the Growth dimension input. This works from day one, is deterministic, and is interpretable without statistical background.

Prophet is deferred to **Phase 1.5**, triggered when ≥30 days of real NicheSignal history is available.

**Consequences:** Growth scores in the first weeks reflect only recent velocity, not long-run trend projection. This is disclosed in the evaluation plan. Porting to Prophet later requires only changing the `forecasting/scoring.py` growth computation; the rest of the pipeline is unaffected.

---

## ADR-004 — Scoring design: three dimensions, percentile rank, weights 0.41/0.35/0.24

**Date:** 2026-04-23
**Status:** Accepted

**Context:** The original scoring formula used four dimensions (Growth 0.35, Demand 0.30, Novelty 0.20, Competition 0.15). The Competition dimension relies on app-store saturation data, which is mocked in Phase 1. Scoring 15% of the total on synthetic data risks self-deception about niche competitiveness.

Normalisation was described as "0–100" without specifying the method, which is where most scoring systems produce inconsistent rankings.

**Decision:**

**Dimensions (Phase 1):**
- Growth: 0.41 — 7-day rolling slope on signal counts
- Demand: 0.35 — mention count, GitHub star delta, install proxy
- Novelty: 0.24 — `1 − (age_of_newest_signal_days / 30)`, clamped [0, 1]

Competition is dropped from Phase 1 and reintroduced in Phase 1.5 when a real app-store provider is integrated. The 0.15 weight is redistributed proportionally across the three remaining dimensions.

**Normalisation:** Percentile rank over a rolling 30-day window per niche. A niche at the 90th percentile on Growth receives a Growth score of 90. Stable across day-to-day outliers; interpretable without statistics knowledge.

**Spike alert:** fires once daily immediately after the niche scoring job. Compares today's `score_total` against the last persisted row in `NicheScoreHistory`. Threshold: +15 points (configurable).

**Consequences:** Phase 1 competition signal is absent from scores. This is explicitly disclosed in the code and evaluation plan. Re-adding Competition requires changing the weights and updating `config.py` — no schema changes needed.

**Implementation notes (M3):** Niches with fewer than 2 days of score history in the 30-day window receive a neutral percentile rank of 50.0 for each dimension. This prevents new niches from being unfairly ranked at 0 before history accrues, and avoids artificially inflating them to 100. The fallback is applied inside `percentile_rank()` in `app/features/trend_features.py` and is covered by `test_percentile_rank_insufficient_history_returns_neutral_50`.

---

## ADR-005 — Agent graph design (LangGraph)

**Status:** Accepted (2026-04-27)

**Context.** M4 needed a transparent, testable orchestration layer that turns
the daily scoring output into an `OpportunityBrief`. The project committed to
LangGraph from day one (project doc §4) and a single asyncio loop (ADR-002).

**Decision.**

1. **Linear graph, no conditional edges.** `fetcher → retriever → forecaster
   → reporter → reviewer → END`. Conditional/branching edges are deferred to
   Phase 2.
2. **Forecaster reads, doesn't recompute.** Reads the latest
   `NicheScoreHistory` row for the day. Computes via `score_niche()` only as
   a cold-start fallback. This avoids duplicating M3 work inside the agent
   and keeps the graph cheap to invoke on demand.
3. **Headline programmatic, summary LLM.** The headline is `f"{name} —
   Score {round(score)}"`; only the prose summary is generated by qwen2.5.
   This sidesteps JSON-schema fragility (Risk Register §16).
4. **Reviewer is heuristic, never retries.** Checks summary length,
   placeholder markers, and evidence count. Sets `has_issues` and logs gaps.
   Retry/repair logic is deferred to Phase 2.
5. **LLM adapter injected at build time.** `build_graph(adapter)` binds the
   adapter via closure. Tests pass `MockLLMAdapter`; the scheduler picks
   `OllamaAdapter` or `MockLLMAdapter` based on `settings.llm_provider`. No
   global singleton.
6. **Persistence in the orchestrator.** `run_brief_for_niche()` invokes the
   compiled graph and writes `OpportunityBrief` (delete-then-insert per
   `(niche_id, day(UTC))`). Nodes stay pure with respect to the brief table.
7. **Per-niche timeout 90s.** Reporter wraps `adapter.generate_brief()` in
   `asyncio.wait_for(timeout=settings.brief_per_niche_timeout_s)`. Timeout
   produces an empty-summary brief that the reviewer marks `has_issues`.
   APScheduler `max_instances=1` prevents overlapping daily runs.

**Consequences.**

- The agent is fully testable with `MockLLMAdapter`; CI never needs Ollama.
- The reviewer can't repair briefs — the `has_issues` flag is observable,
  and Phase 2 can add a self-correction loop without changing the schema.
- Brief generation is bounded: 12 niches × 90s ≤ 18 min wall-clock.
- Adding a new dimension (e.g. competition in Phase 1.5) requires no graph
  changes — only `app/forecasting/scoring.py` and the prompt template.
