# Plan — v4.C Review Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.
>
> **Environment note:** Same Windows/WSL2 + `uv` constraints as Plans A/B/C. Pass commands to the user; no direct git commits.

**Goal:** Close the gaps surfaced by the post-Plan-C codebase review (2026-05-06). Restore the embedding-model isolation feature (currently dead code), fix v4 brief generation (currently `KeyError` on every real adapter call), delete v3 scripts that now crash on import, and tighten the secondary issues (recluster heuristics, scheduler leaks, missing tests, `.env.example` drift).

**Spec/plan references:**
- Plan C: `docs/superpowers/plans/2026-04-28-v4c-playstore-and-decommissioning.md`
- ADR-011: `docs/decisions.md`
- Spec §4.3: `docs/superpowers/specs/2026-04-28-opportunity-discovery-pivot-design.md`

**Depends on:** Plans A, B, C all complete and merged on `main`.

**Tech stack:** No new dependencies. Pure code/test/doc changes.

---

## Context

A full review of `main` against Plan C (see chat transcript dated 2026-05-06) found two breaking bugs and ~10 smaller gaps. The two breaking bugs are silent in CI because:
- The brief-generation bug only surfaces when a real `LLMAdapter` is used; tests use `MockLLMAdapter` which short-circuits the broken prompt path.
- The embedding-model isolation bug is silent because `PainPoint.embedding_model` is always NULL — every comparison "works" (against the NULL bucket), so no test fails. The bug only matters in production once an Ollama-embedded population coexists with a NIM-embedded one.

This plan fixes both, plus the surrounding cleanup, so v4 is actually feature-complete in behaviour, not just file presence.

---

## File-Level Plan

**Modified:**
- `app/pipeline/embed.py` — set `PainPoint.embedding_model` after each embed call.
- `app/pipeline/clustering.py` — set `OpportunityCandidate.embedding_model` from the cluster's pain-points.
- `app/pipeline/identity_resolution.py` — build per-model `EmbeddingIndex` once; remove duplicate import.
- `app/pipeline/recluster.py` — fix `_check_relabel_needed` to use a real labelling timestamp; align the merge pass with spec §4.3 (use new clusters, not all-pairs); confirm new candidates carry `embedding_model`.
- `app/pipeline/brief_generation.py` — build the v4 context that the prompt template actually expects.
- `app/llm/prompts.py` — rewrite `render_brief_prompt` for the v4 candidate shape (problem statement, audience, why_now, evidence, score breakdown), drop "niche/scorecard/forecast" plumbing.
- `app/ingestion/scheduler.py` — close `httpx.AsyncClient` instances in Play Store + iOS jobs; document the cron-ordering choice.
- `.env.example` — add the missing v4 keys.
- `scripts/playstore_spike.py` — fix Notion app id.
- `docs/decisions.md` — append ADR-012 if the merge-pass change alters spec interpretation, otherwise patch ADR-011 with a clarifying note.

**Deleted:**
- `scripts/run_ingestion.py` — imports deleted v3 modules.
- `scripts/run_replay.py` — imports deleted v3 modules.
- `scripts/run_scoring.py` — imports deleted v3 modules.
- `app/llm/openai_adapter.py` — dead code, never imported.
- `app/llm/anthropic_adapter.py` — dead code, never imported.
- Any tests that exclusively cover the deleted scripts/adapters.

**New tests:**
- `tests/pipeline/test_embed.py` — assert `pp.embedding_model` is set to `embedder.model_name`.
- `tests/pipeline/test_clustering.py` — assert new candidates inherit `embedding_model` from their pain-points.
- `tests/pipeline/test_identity_resolution.py` — add a regression test that cross-model pain-points don't match each other (currently passes vacuously because everything is NULL).
- `tests/pipeline/test_recluster.py` — add the missing `test_recluster_splits_overbroad_candidate` from Plan C-08.
- `tests/pipeline/test_brief_generation.py` — add a non-mock smoke test that exercises `render_brief_prompt` (use a fake LLM adapter that just calls `render_brief_prompt(context)` and returns the rendered text — proves no `KeyError`).
- `tests/integration/test_fresh_deploy.py` — extend with the missing C-18 scenarios (digest job, feedback callback, second-day pipeline run, bulk_backfill).

---

## Tasks

### F-01 — Tag pain-points with `embedding_model` at embed time

**Why this is breaking:** Without this, ADR-011's per-model isolation is a no-op. Cross-provider deployment will silently compare 768-dim Ollama vectors against 1024-dim NIM centroids the moment a user switches providers.

**Files:**
- `app/pipeline/embed.py`
- `tests/pipeline/test_embed.py` (new or extend if exists)

**Steps:**

1. In `run_embedding`, after `pp.embedding = vec`, add `pp.embedding_model = embedder.model_name`. Apply to every pain-point in the batch.
2. The `embedder.model_name` lookup is cheap (property), but cache it in a local variable above the batch loop to make the intent explicit.
3. Add/extend the test:
   ```python
   async def test_embed_tags_pain_points_with_model_name(session) -> None:
       embedder = MockEmbeddingAdapter()  # model_name == "mock-embed-v1" or similar
       # seed PainPoint(s) with embedding=None
       await run_embedding(session, embedder)
       pps = (await session.execute(select(PainPoint))).scalars().all()
       assert all(pp.embedding_model == embedder.model_name for pp in pps)
   ```

**Verification:** Run `tests/pipeline/test_embed.py` — green.

**Suggested commit:** `fix(pipeline): tag pain-points with embedding_model at embed time`

---

### F-02 — Inherit `embedding_model` on new candidates from their cluster's pain-points

**Why:** The cluster IS its pain-points; whatever model embedded them defines the centroid's space. Recluster relies on this column to group correctly. Without it, identity-resolution + recluster both treat every centroid as belonging to the NULL model.

**Files:**
- `app/pipeline/clustering.py`
- `tests/pipeline/test_clustering.py`

**Steps:**

1. In `run_clustering` (or wherever `OpportunityCandidate(...)` is constructed in `clustering.py:95-101`), read `cluster_pps[0].embedding_model` and pass it as `embedding_model=...` to the constructor.
2. Defensive sanity check: `assert all(pp.embedding_model == cluster_pps[0].embedding_model for pp in cluster_pps)` — if it fails, the embedding step missed pain-points and we'd be co-clustering across models. Log a warning + skip that cluster rather than asserting in production. (Use `log.error("clustering_mixed_embedding_models", ...)` and `continue`.)
3. Extend `tests/pipeline/test_clustering.py` with `test_clustering_propagates_embedding_model`. Seed 4+ pain-points with embeddings and `embedding_model="mock-embed-v1"`; run clustering; assert the new candidate's `embedding_model == "mock-embed-v1"`.
4. Add `test_clustering_skips_mixed_model_clusters` — pre-seed pain-points with two different `embedding_model` values; assert no candidate is created (or one per model, depending on whether the embed stage would have ever produced this; defensive only).

**Verification:** Tests green. Spot-check: `grep -n "embedding_model" app/pipeline/clustering.py` shows the assignment.

**Suggested commit:** `fix(pipeline): propagate embedding_model from pain-points to new candidates`

---

### F-03 — Fix `render_brief_prompt` for the v4 candidate context

**Why this is breaking:** Every production brief raises `KeyError` because the template reads v3 keys (`niche`, `scorecard`, `forecast`) but `brief_generation.py` provides v4 keys. CI doesn't catch it because the only adapter exercised in tests is `MockLLMAdapter`, which doesn't call `render_brief_prompt`.

**Files:**
- `app/llm/prompts.py`
- `app/pipeline/brief_generation.py`
- `tests/pipeline/test_brief_generation.py` (extend)

**Steps:**

1. Rewrite `_BRIEF_TEMPLATE` and `render_brief_prompt` in `app/llm/prompts.py` to accept the v4 context shape: `problem_statement`, `audience`, `why_now`, `evidence` (list of `{source_type, source_url, excerpt, problem_text, audience, extracted_at}`). Optionally include score breakdown if `brief_generation.py` adds it (see step 3) — but don't require it.
2. New template should produce a 3-5 sentence brief. Drop the v3 "Composite score / Growth / Demand / Novelty / Trend direction" framing entirely; v4 doesn't have those dimensions. Reference the candidate by problem statement, name the audience, anchor at least one evidence item by source type.
3. In `app/pipeline/brief_generation.py:82-87`, pass the score breakdown into the context if it's already loaded by the caller (look at `_digest_job` in scheduler — it loads scoring data; if so, thread `score_breakdown` through). If not threaded, leave it out — the prompt should not require it.
4. Extend `tests/pipeline/test_brief_generation.py` with a regression test that uses a tiny custom `LLMAdapter` whose `generate_brief` simply calls `render_brief_prompt(context)` and returns the rendered string. Assert the call succeeds (no `KeyError`) and the rendered text contains the candidate's problem statement and at least one evidence source type.
5. Delete or update the v3 `EXTRACT_PROMPT` shim at `prompts.py:36-59` if no caller still uses it — `grep -rn "EXTRACT_PROMPT\b"` to confirm.

**Verification:** New test green. `OllamaAdapter.generate_brief` and `NvidiaNimAdapter.generate_brief` both source-import the new template; manually trace the import path to confirm.

**Suggested commit:** `fix(brief): rewrite brief prompt for v4 candidate context`

---

### F-04 — Delete v3 scripts that import deleted modules

**Why:** `scripts/run_ingestion.py`, `scripts/run_replay.py`, `scripts/run_scoring.py` all crash on `python -m scripts.run_*` because they import `app.features.niche_builder`, `app.ingestion.appstore_mock_connector`, `app.forecasting.scoring`, and the deleted `Niche*` models. Plan C-10 cleared production code but missed `scripts/`.

**Files:**
- `scripts/run_ingestion.py` — DELETE.
- `scripts/run_replay.py` — DELETE.
- `scripts/run_scoring.py` — DELETE.
- `tests/test_run_backfill_cli.py` — verify it doesn't reference any of the deleted scripts; should target `scripts/run_backfill.py` only.
- `README.md`, `docs/roadmap.md`, `devtrend-project-document.md` — search for any remaining mentions and update or remove.

**Steps:**

1. Confirm no test imports them: `grep -rn "run_ingestion\|run_replay\|run_scoring" tests/`.
2. Confirm no doc tells users to run them: `grep -rn "run_ingestion\|run_replay\|run_scoring" docs/ README.md devtrend-project-document.md`. The historical references in `docs/m2-implementation-plan.md` and `docs/m3-implementation-plan.md` are fine to keep (those are Plan-1.5/v3 archived design docs, not user-facing instructions).
3. `rm scripts/run_ingestion.py scripts/run_replay.py scripts/run_scoring.py`.
4. Run `uv run pytest` to confirm nothing breaks.

**Verification:** `ls scripts/` shows only `migrate_to_v4_2.py`, `playstore_spike.py`, `run_backfill.py`. Tests still green.

**Suggested commit:** `chore(scripts): delete v3 manual-runner scripts (broken since C-10)`

---

### F-05 — Delete dead `openai_adapter.py` / `anthropic_adapter.py`

**Why:** `app/llm/openai_adapter.py` and `app/llm/anthropic_adapter.py` are never imported and never routed by `make_llm_adapter`. Plan A claimed they were pre-routed; they were not. Dead code attracts confusion ("which adapter does the system use?").

**Files:**
- `app/llm/openai_adapter.py` — DELETE.
- `app/llm/anthropic_adapter.py` — DELETE.
- Any test that targets them — `tests/llm/test_*` — DELETE if exclusively for these adapters.

**Steps:**

1. `grep -rn "openai_adapter\|anthropic_adapter\|OpenAIAdapter\|AnthropicAdapter" app/ tests/` — confirm no live import.
2. Delete the two files.
3. Confirm `tests/llm/test_adapter_interface.py` and friends still pass (they exercise the ABC, not these implementations).
4. If a future need for OpenAI/Anthropic arises, re-add them via a fresh PR with factory routing + tests. The "ghost adapters" strategy is worse than no adapter at all.

**Suggested commit:** `chore(llm): remove unused openai/anthropic adapters (never routed)`

---

### F-06 — Add the missing C-08 split test

**Why:** Plan C-08 specified six tests; the codebase has five. The split path is uncovered.

**Files:**
- `tests/pipeline/test_recluster.py`

**Steps:**

1. Add `test_recluster_splits_overbroad_candidate`. Seed:
   - One source item.
   - One `OpportunityCandidate` with `centroid=[0.5, 0.5, 0.0]` (between the two future sub-clusters).
   - 6 pain-points: 3 close to `[1.0, 0.0, 0.0]` and 3 close to `[0.0, 1.0, 0.0]`, all attached to the same candidate, same `embedding_model`.
2. Run `run_weekly_recluster(session, merge_threshold=0.95, split_silhouette_threshold=0.5, min_cluster_size=2)`. The high `split_silhouette_threshold` ensures the cohesion check fails (cohesion will be ~0.5 between the two sub-clusters).
3. Assert `report.split_count >= 1`. Assert one new `OpportunityCandidate` row exists in addition to the original. Assert the original retains 3 pain-points (the largest sub-cluster), and the new candidate has the other 3.
4. Note: the current implementation's split heuristic uses `cohesion < threshold` — if the test reveals the threshold tuning is off, fix the heuristic in `recluster.py` rather than warping the test.

**Verification:** New test green. Run `tests/pipeline/test_recluster.py` in full.

**Suggested commit:** `test(recluster): add split-overbroad-candidate coverage (C-08 backfill)`

---

### F-07 — Fix `_check_relabel_needed` heuristic

**Why:** The heuristic uses `cand.last_evidence_at` as a proxy for "last labelled at", but `last_evidence_at` is updated on every pain-point attachment in identity resolution. So `recent_count = sum(... if pp.extracted_at > last_evidence_at)` is approximately always 0 → the heuristic almost never fires.

**Files:**
- `app/pipeline/recluster.py`
- `tests/pipeline/test_recluster.py`
- Possibly `app/models.py` (if a `last_labelled_at` column is added)

**Steps:**

1. Two options:
   - **(a) Use the latest `CandidateBrief.generated_at` as the proxy for last labelling.** Briefs are generated at digest time, after labelling. Compare PP `extracted_at` against the latest brief's `generated_at`.
   - **(b) Add `OpportunityCandidate.last_labelled_at: datetime | None` and set it in `app/pipeline/labelling.py` when `labeller_model` is assigned.** Cleaner and decouples labelling from briefing; minor migration cost (one column).
2. Pick (b). It's a one-column migration but produces an honest signal. Add to `app/models.py`, extend `scripts/migrate_to_v4_2.py` with a fresh `ALTER TABLE opportunity_candidates ADD COLUMN last_labelled_at` (the script is idempotent).
3. In `app/pipeline/labelling.py`, set `candidate.last_labelled_at = datetime.now(UTC)` right next to `candidate.labeller_model = llm.model_name`.
4. In `_check_relabel_needed` in `recluster.py`, replace `last_labelled_at = cand.last_evidence_at` with `last_labelled_at = cand.last_labelled_at`. Skip candidates where this is `None`.
5. Add `test_recluster_relabels_when_30pct_evidence_is_post_labelling` to `tests/pipeline/test_recluster.py`. Seed a candidate with `last_labelled_at` set 10 days ago; attach 4 pain-points (2 with `extracted_at` before that timestamp, 2 after); assert `report.relabelled_count >= 1` and `cand.problem_statement == "[unlabelled]"`.

**Verification:** Migration runs idempotently. Test green.

**Suggested commit:** `fix(recluster): use last_labelled_at for relabel heuristic`

---

### F-08 — Align recluster merge pass with spec §4.3

**Why (defer if scope-tight):** The current `_recluster_for_model` merge loop compares all pairs of existing centroids. Spec says: re-cluster from scratch, then for each new cluster, find the best-matching active candidate; if multiple candidates map to the same new cluster, merge them. The current behaviour is reasonable and tests pass, but the new clusters computed at line 100-107 are unused for merge decisions. ADR-011 documents the spec behaviour, not the implementation.

**Files:**
- `app/pipeline/recluster.py`
- `tests/pipeline/test_recluster.py`
- `docs/decisions.md` — clarify ADR-011 if we keep all-pairs.

**Decision gate:**

| Choice | Action |
|---|---|
| Implement spec faithfully (preferred) | Replace the all-pairs loop with: for each new cluster (label != -1), compute `cluster_mean`, find candidates whose centroids are within `merge_threshold` of `cluster_mean`; if ≥2, merge them (keep the one with the most pain-points; archive others; reattach pain-points; recompute centroid). Update tests if the simulated drift cases need different fixtures. |
| Document the divergence (cheaper) | Keep the all-pairs implementation. Add a one-paragraph note to ADR-011 explaining that the v1 merge pass is "any two candidates with similar centroids" rather than the spec's "candidates that re-cluster into the same group." Open a follow-up issue to align later. |

**Recommendation:** Pick the document-the-divergence path for this fix plan; it's lower risk and the all-pairs behaviour has tests already. Open a TODO in the ADR. Re-implement the spec version in a future plan (v4.D housekeeping or similar).

**Verification:** ADR-011 reads consistently with what the code actually does. No test changes.

**Suggested commit:** `docs(adr): clarify ADR-011 merge-pass semantics (matches v1 implementation)`

---

### F-09 — Close `httpx.AsyncClient` instances in scheduler jobs

**Why:** `_playstore_ingestion_job` (`app/ingestion/scheduler.py:153-175`) and `_ios_rss_ingestion_job` (224-243) instantiate `httpx.AsyncClient(...)` per fire and never `aclose()`. APScheduler runs these on a daily cron, so the leak accumulates.

**Files:**
- `app/ingestion/scheduler.py`

**Steps:**

1. Wrap each connector instantiation in `async with httpx.AsyncClient(...) as client:` so closure happens automatically. Pattern:
   ```python
   async def _playstore_ingestion_job() -> None:
       from app.ingestion.playstore_connector import PlayStoreReviewsConnector
       async with httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s) as client:
           connector = PlayStoreReviewsConnector(client=client, registry=registry)
           try:
               await asyncio.wait_for(connector.run(), timeout=settings.ingestion_job_timeout_s * 4)
           except asyncio.TimeoutError:
               registry.mark_error("playstore", "job timed out", settings.ingestion_job_timeout_s * 4)
               log.error("Play Store ingestion job timed out")
           except Exception as exc:
               log.error("Play Store ingestion job crashed", error=str(exc))
   ```
2. Apply same pattern to `_ios_rss_ingestion_job`.
3. The Play Store connector itself doesn't use `client` (it calls `google_play_scraper` via `to_thread`), but the connector base requires one — that's fine, the contract holds.

**Verification:** `grep -n "httpx.AsyncClient" app/ingestion/scheduler.py` shows no leaks. No tests required (resource lifecycle is hard to assert under pytest); a code review of the diff is sufficient.

**Suggested commit:** `fix(scheduler): close httpx clients in Play Store + iOS RSS jobs`

---

### F-10 — Build identity-resolution per-model index once per model, not per pain-point

**Why:** Current code rebuilds the `EmbeddingIndex` inside the `for pp in unattached:` loop (`identity_resolution.py:67-76`). With M candidates and N pain-points, this is O(N·M) construction. Should be O(M) construction + O(N·M) queries.

**Files:**
- `app/pipeline/identity_resolution.py`

**Steps:**

1. After grouping `candidates_by_model`, build a parallel `index_by_model: dict[str | None, EmbeddingIndex]` once. Look up by `pp.embedding_model` inside the loop.
2. Remove the duplicate `from collections import defaultdict` at line 48 (already imported at line 3).

**Verification:** Existing identity-resolution tests still pass. No new test required (behaviour is identical, only performance changes); but if there's a quick assertion about index construction count, add it.

**Suggested commit:** `perf(identity_resolution): build per-model index once, dedupe import`

---

### F-11 — Fix `.env.example` and add a regression check

**Why:** Several v4 keys are missing: `NIM_BASE_URL`, `PLAYSTORE_TOP_N_PER_CATEGORY`, `PLAYSTORE_REVIEWS_PER_APP`, `PLAYSTORE_CRON_HOUR`, `ENABLE_IOS_RSS`, `WEEKLY_RECLUSTER_CRON_HOUR`, `WEEKLY_RECLUSTER_CRON_DAY`. Quick-start docs reference variables that aren't there.

**Files:**
- `.env.example`
- `tests/test_config.py` (extend)

**Steps:**

1. Add the missing keys to `.env.example` with their default values commented in. Mirror the order of `app/config.py:Settings`.
2. Add `test_env_example_covers_all_settings` to `tests/test_config.py`:
   ```python
   def test_env_example_covers_settings_with_non_default_or_secret_values():
       env_path = Path(__file__).parent.parent / ".env.example"
       env_keys = {line.split("=")[0].strip() for line in env_path.read_text().splitlines()
                   if line.strip() and not line.strip().startswith("#") and "=" in line}
       # Pick a small list of keys we *require* in .env.example for ops visibility:
       required = {
           "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS",
           "LLM_PROVIDER", "EMBEDDING_PROVIDER",
           "NIM_API_KEY", "NIM_BASE_URL", "NIM_LLM_MODEL", "NIM_EMBEDDING_MODEL",
           "PLAYSTORE_CRON_HOUR", "PLAYSTORE_TOP_N_PER_CATEGORY", "PLAYSTORE_REVIEWS_PER_APP",
           "ENABLE_IOS_RSS",
           "WEEKLY_RECLUSTER_CRON_HOUR", "WEEKLY_RECLUSTER_CRON_DAY",
           "IDENTITY_RESOLUTION_THRESHOLD",
       }
       missing = required - {k.upper() for k in env_keys}
       assert not missing, f"missing in .env.example: {missing}"
   ```
3. The test guards against future drift — anyone adding a new key now also has to add it to the example.

**Verification:** New test green.

**Suggested commit:** `chore(env): backfill missing v4 keys in .env.example + drift guard`

---

### F-12 — Decide Play Store cron timing

**Why:** `playstore_cron_hour=2` (02:00 UTC) but pipeline is 03:30 UTC. Plan C-17 said "after pipeline + scoring." Today's reviews land in *next* day's pipeline.

**Files:**
- `app/config.py` (default change) OR `app/ingestion/scheduler.py` (note)

**Decision gate:**

| Choice | Reasoning |
|---|---|
| Move to after pipeline (e.g. 04:00 UTC) | Same-day reviews flow into same-day digest. But: ingestion + extraction in one window means the extraction stage of the pipeline misses today's Play Store data unless scheduled even later. |
| Keep at 02:00 UTC | Reviews ingested early; pipeline at 03:30 picks them up; one-day lag is acceptable and matches Reddit/HN/GitHub interval scheduling (they run every 6-12h). |

**Recommendation:** Keep at 02:00 UTC. This is consistent with the other interval-based connectors and avoids cramming ingestion + extraction into a tight window. Update the docstring/comment in `scheduler.py` to make the choice explicit ("Play Store ingests at 02:00 UTC; daily pipeline at 03:30 picks up the new SourceItems"). Patch the Plan C-17 plan note in this file's "Resolved" section if needed.

**Suggested commit:** `chore(scheduler): document Play Store cron ordering rationale`

---

### F-13 — Backfill the C-18 integration test scenarios

**Why:** Plan C-18 specified 9 end-to-end scenarios; the codebase has ~3. The integration test as-shipped is a smoke test, not a fresh-deploy walkthrough.

**Files:**
- `tests/integration/test_fresh_deploy.py`

**Steps:**

Extend `test_fresh_deploy_pipeline` (or add separate `@pytest.mark.integration` tests) covering:
1. **Migration step.** Call `scripts.migrate_to_v4_2.migrate(database_url)` on a fresh `:memory:` DB before any other setup. Assert columns exist via `PRAGMA table_info`.
2. **Bulk backfill.** Replace the manual `_seed_source_items` with a call to `app.ingestion.backfill.bulk_backfill` using mock connectors that yield 30 days of synthetic data. (Mock connectors can be tiny inline classes inheriting `BaseConnector`.)
3. **Briefs.** After the score step, call `generate_briefs_for(session, llm, top_3_candidates)` and assert ≥1 `CandidateBrief` row exists. Use `MockLLMAdapter` (which returns a fixed string).
4. **Digest job.** Mock the bot (`AsyncMock(spec=...)`) and call `_digest_job` (or `run_digest_job` directly). Assert `bot.send_message` was called with text containing the top candidate's problem statement.
5. **Feedback callback.** Construct a fake telegram `CallbackQuery` with `data="fb:up:<candidate_id>"` and route it to the feedback handler. Assert one `CandidateFeedback` row was inserted.
6. **Second-day pipeline.** Run `run_pipeline` again with new fixture data; assert (a) some new pain-points attached to existing candidates (identity resolution working) and (b) some new candidates formed (clustering still runs on genuinely-new evidence).

Mark all of these `@pytest.mark.integration` so they're excluded from the default run.

**Verification:** `uv run pytest -m integration tests/integration/test_fresh_deploy.py` runs all scenarios green.

**Suggested commit:** `test(integration): backfill C-18 fresh-deploy scenarios`

---

### F-14 — Cosmetic / hygiene cleanup

**Why:** Small drift items.

**Files:**
- `scripts/playstore_spike.py` — change `com.notion.id` → `com.notion.so` (line 18).
- Any leftover `__pycache__` or `*.pyc` reference in tests (none expected; double-check with `git status`).
- Verify `pyproject.toml` does not still reference `langgraph` / `langchain-core` (already absent — Plan C did this).

**Steps:**

1. Edit `scripts/playstore_spike.py`.
2. Run `uv run ruff check app/ tests/ scripts/` — fix anything new.
3. Run `uv run mypy app/` — fix any new errors that the F-01..F-10 changes might surface.

**Suggested commit:** `chore: minor hygiene (notion app id, lint sweep)`

---

## Definition of Done — review fixes

- [ ] F-01: every `PainPoint` written by `run_embedding` carries `embedding_model = embedder.model_name`.
- [ ] F-02: every `OpportunityCandidate` created by `run_clustering` carries `embedding_model` matching its pain-points.
- [ ] F-03: `OllamaAdapter.generate_brief` and `NvidiaNimAdapter.generate_brief` succeed against a v4 candidate context (no `KeyError`); regression test exists.
- [ ] F-04: `scripts/run_ingestion.py`, `run_replay.py`, `run_scoring.py` are deleted; no doc references them as live commands.
- [ ] F-05: `app/llm/openai_adapter.py` and `anthropic_adapter.py` are deleted.
- [ ] F-06: `test_recluster_splits_overbroad_candidate` exists and passes.
- [ ] F-07: `last_labelled_at` column added; `_check_relabel_needed` uses it; regression test added.
- [ ] F-08: ADR-011 reads consistent with implementation OR merge pass rewritten to spec.
- [ ] F-09: scheduler jobs use `async with httpx.AsyncClient(...)` — no leaks.
- [ ] F-10: identity resolution builds index once per model.
- [ ] F-11: `.env.example` complete; drift-guard test in place.
- [ ] F-12: cron ordering documented (or moved).
- [ ] F-13: C-18 integration scenarios all present and green under `-m integration`.
- [ ] F-14: lint + mypy clean; cosmetic items fixed.
- [ ] Full test suite green: `uv run pytest` (default) and `uv run pytest -m integration`.

---

## Suggested execution order

Sequential dependencies suggest this order (adjacency = "this task either touches the same file or assumes the previous fix"):

1. F-01 → F-02 (both about embedding_model; F-02 depends on F-01 producing real values).
2. F-03 (independent; unblocks production briefs).
3. F-04 → F-05 (deletions; independent of each other but both pure deletes).
4. F-06 → F-07 → F-08 (recluster cluster of changes; F-07 changes schema so it runs before F-08 to avoid two migrations).
5. F-09 → F-10 (small refactors).
6. F-11 → F-12 (config + cron).
7. F-13 (depends on F-01/F-02/F-03 working — integration test will exercise them).
8. F-14 (last — sweep).

Total scope: ~1–1.5 working days for a focused implementer.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| F-01/F-02 backfill: existing rows in a long-running deployment have `embedding_model = NULL` | Add a one-shot script `scripts/backfill_embedding_model.py` (or a step inside `migrate_to_v4_2.py`) that sets `embedding_model = '<configured embedder>.model_name'` for all rows where it's NULL, **only if** the deployment has never switched providers. Document the assumption. |
| F-03 prompt rewrite changes brief quality | The brief prompt is tunable by definition. Land the fix; iterate on tone in a follow-up after seeing real outputs. Don't gate this fix on prompt-engineering perfection. |
| F-07 schema migration adds a new column on a deployed DB | `migrate_to_v4_2.py` is already idempotent (catches "duplicate column"). Add the new ALTER and re-run. No data loss. |
| F-13 integration test becomes flaky | Mock everything with deterministic fixtures: fixed timestamps via `freezegun` if needed, `MockLLMAdapter` and `MockEmbeddingAdapter` for all LLM calls, recorded `google_play_scraper.reviews()` payloads. Keep it `@pytest.mark.integration` so it doesn't run on every push. |
| Removing openai/anthropic adapters in F-05 turns out to be load-bearing somewhere | The grep in step 1 of F-05 is the safety net. If it returns hits beyond the file itself, stop and re-evaluate. |

---

*End of v4.C review-fixes plan.*
