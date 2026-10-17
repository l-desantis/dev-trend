# Staleness Fix — Identity-Resolution Threshold + Lifecycle Immortality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore evolving daily digests by re-calibrating identity resolution for the production embedding space and fixing the lifecycle bug that makes evidence-less candidates immortally "hot".

**Architecture:** Two independent, well-grounded fixes. (1) Lower `identity_resolution_threshold` from 0.82 (tuned for Ollama `nomic-embed-text`) to 0.65 (measured-correct for prod `nvidia/nv-embedqa-e5-v5`), so Stage-3 identity resolution actually attaches new pain points to existing candidates — restoring evidence accumulation, hence momentum/frequency score dynamics, hence digest movement. (2) In `derive_lifecycle_state`, treat a `None` `last_evidence_at` as the candidate's `created_at` (not "age 0"), so candidates that never receive fresh evidence go `dormant` after 14 days instead of staying `hot` forever.

**Tech Stack:** Python 3.12, SQLAlchemy async, pytest (async), pydantic-settings, numpy.

## Global Constraints

- **Claude cannot run `uv`/`python` in this shell** (per project CLAUDE.md). Every "run the test" step must be executed by the **user**, who pastes output back. Phrase test runs as `uv run pytest ...` for the user to run.
- Platform: Windows 11 + WSL2; the env is managed by `uv`.
- Follow existing test patterns: async tests use the `session` fixture from `tests/pipeline/conftest.py`; no new fixtures needed.
- Conventional-commit messages (`fix:`, `feat:`, `test:`).
- **Calibration source of truth:** `identity_resolution_threshold = 0.65`. Derived from `scripts/diagnose_attachment.py` run against prod 2026-06-24: true-positive band p50 0.704 / p25 0.656; the 16 genuinely-novel unattached pain points cap at cosine 0.622. 0.65 sits in the trough — above the novel ceiling, retaining the bulk of true positives.
- The threshold value is **observable, not final**: after deploy, Stage-3 attachment rate should climb from ~0/run to a meaningful fraction. The constant lives in one place (`app/config.py`) so it can be re-tuned without code changes elsewhere.

---

### Task 1: Re-calibrate `identity_resolution_threshold` to 0.65

**Files:**
- Modify: `app/config.py:155`
- Test: `tests/pipeline/test_identity_resolution.py` (add one test)
- Test: `tests/test_config.py` (add one guard test)

**Interfaces:**
- Consumes: `run_identity_resolution(session, *, threshold: float) -> IdentityResolutionReport` (existing; unchanged signature). `EmbeddingIndex.nearest(vector, k, threshold)` normalises vectors internally, so cosine is computed on unit vectors.
- Consumes: `app.config.get_settings()` returns a cached `Settings`; call `get_settings.cache_clear()` before asserting the default.
- Produces: nothing new — only the default value of `Settings.identity_resolution_threshold` changes from `0.82` to `0.65`.

- [ ] **Step 1: Write the failing behavioural test**

Add to `tests/pipeline/test_identity_resolution.py`. A pain point whose cosine to the only candidate centroid is ≈0.70 must attach at the recalibrated threshold 0.65, and must NOT attach at the old 0.82 — locking in the trough between the novel-leftover ceiling (0.622) and the true-positive band.

```python
async def test_identity_attaches_in_recalibrated_band(session: AsyncSession) -> None:
    """A ~0.70-cosine pain point attaches at 0.65 (new) but not at 0.82 (old)."""
    item = await _seed_item(session)
    centroid = [1.0] + [0.0] * 31
    candidate = OpportunityCandidate(
        problem_statement="test", specificity=1, centroid=centroid, is_archived=False
    )
    session.add(candidate)
    await session.flush()

    # Unit vector at cosine 0.70 to the centroid: [0.70, sqrt(1-0.49), 0...]
    import math
    embedding = [0.70, math.sqrt(1.0 - 0.49)] + [0.0] * 30
    pp = PainPoint(
        source_item_id=item.id, extractor_model="mock",
        problem_text="moderately similar", audience="users", embedding=embedding,
    )
    session.add(pp)
    await session.commit()

    # Old threshold rejects it
    report_old = await run_identity_resolution(session, threshold=0.82)
    assert report_old.attached == 0

    # Recalibrated threshold attaches it
    report_new = await run_identity_resolution(session, threshold=0.65)
    assert report_new.attached == 1
    pp_refreshed = (await session.execute(select(PainPoint))).scalar_one()
    assert pp_refreshed.candidate_id == candidate.id
```

- [ ] **Step 2: Run the test to verify it fails**

Run (user executes): `uv run pytest tests/pipeline/test_identity_resolution.py::test_identity_attaches_in_recalibrated_band -v`
Expected: PASS already (this test exercises explicit threshold params, not the config default, so it validates the mechanism). If it FAILS, the embedding/cosine assumption is wrong — stop and inspect `EmbeddingIndex.nearest` normalisation before proceeding.

> Note: this behavioural test does not depend on the config default; it guards the *mechanism*. The config default is guarded by Step 3's test, which is the one that genuinely fails before the change.

- [ ] **Step 3: Write the failing config-default guard test**

Add to `tests/test_config.py`:

```python
def test_identity_resolution_threshold_is_recalibrated_for_e5() -> None:
    """Prod runs nvidia/nv-embedqa-e5-v5; 0.82 (nomic) is unreachable there.

    Calibrated 2026-06-24 via scripts/diagnose_attachment.py: TP band p50 0.704,
    novel-leftover ceiling 0.622 → 0.65 sits in the trough.
    """
    from app.config import get_settings
    get_settings.cache_clear()
    assert get_settings().identity_resolution_threshold == 0.65
```

- [ ] **Step 4: Run it to verify it fails**

Run (user executes): `uv run pytest tests/test_config.py::test_identity_resolution_threshold_is_recalibrated_for_e5 -v`
Expected: FAIL — `assert 0.82 == 0.65`.

- [ ] **Step 5: Change the config default**

In `app/config.py`, change line 155 from:

```python
    identity_resolution_threshold: float = 0.82
```

to:

```python
    # Calibrated for prod embedding space nvidia/nv-embedqa-e5-v5 (2026-06-24).
    # 0.82 was tuned for Ollama nomic-embed-text and is unreachable under e5
    # (TP band p50 0.704, novel-leftover ceiling 0.622). See
    # scripts/diagnose_attachment.py and docs/superpowers/plans/2026-06-24-*.
    identity_resolution_threshold: float = 0.65
```

- [ ] **Step 6: Run both new tests + the existing identity suite to verify pass**

Run (user executes): `uv run pytest tests/test_config.py::test_identity_resolution_threshold_is_recalibrated_for_e5 tests/pipeline/test_identity_resolution.py -v`
Expected: all PASS (the four pre-existing identity tests pass explicit thresholds, so they are unaffected).

- [ ] **Step 7: Commit**

```bash
git add app/config.py tests/pipeline/test_identity_resolution.py tests/test_config.py
git commit -m "fix(identity): recalibrate identity_resolution_threshold 0.82 -> 0.65 for e5

0.82 was tuned for Ollama nomic-embed-text; prod runs nvidia/nv-embedqa-e5-v5
whose compressed cosine geometry makes 0.82 unreachable, so Stage-3 attachment
fired ~never and recurring themes re-spawned as duplicate candidates daily.
Calibrated via scripts/diagnose_attachment.py (TP band p50 0.704, novel ceiling
0.622).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Fix lifecycle immortality — treat `None` `last_evidence_at` as `created_at`

**Files:**
- Modify: `app/pipeline/lifecycle.py:43-47`
- Test: `tests/pipeline/test_lifecycle.py` (add one test; existing tests unaffected)

**Interfaces:**
- Consumes: `derive_lifecycle_state(candidate: OpportunityCandidate, history: list[CandidateScoreHistory]) -> str | None` (existing; unchanged signature).
- `OpportunityCandidate.created_at` is always set and is already subtracted from `latest.scored_at` at `lifecycle.py:41` (`age_days`), so it is type-compatible (tz-aware vs the same `scored_at`).
- Produces: behavioural change only — a candidate with `last_evidence_at is None` and `created_at` ≥ 14 days before `scored_at` now derives `"dormant"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/pipeline/test_lifecycle.py`. The existing `test_derive_handles_null_last_evidence_at` (age 5 → "emerging") stays valid; this new case proves an *old* evidence-less candidate goes dormant instead of staying hot forever.

```python
def test_derive_null_last_evidence_old_candidate_goes_dormant() -> None:
    """A candidate created 20 days ago with no attached evidence is dormant, not immortal.

    Regression: None last_evidence_at was treated as age 0, so evidence-less
    candidates could never go dormant and stayed 'hot' forever (the staleness bug).
    """
    c = _candidate(age_days=20, last_evidence_days_ago=None)
    history = [_score_row(1, momentum=70, frequency=40)]  # would otherwise be "hot"
    assert derive_lifecycle_state(c, history) == "dormant"
```

- [ ] **Step 2: Run it to verify it fails**

Run (user executes): `uv run pytest tests/pipeline/test_lifecycle.py::test_derive_null_last_evidence_old_candidate_goes_dormant -v`
Expected: FAIL — currently derives `"hot"` (None → last_pp_age 0 → not dormant; momentum 70 + frequency 40 → "hot").

- [ ] **Step 3: Apply the fallback in `derive_lifecycle_state`**

In `app/pipeline/lifecycle.py`, replace lines 43-47:

```python
    last_pp_age = (
        (latest.scored_at - candidate.last_evidence_at).days
        if candidate.last_evidence_at is not None
        else 0
    )
```

with:

```python
    # No attached evidence means the candidate's freshest signal is its birth
    # (the pain points it was clustered from), not "today". Falling back to
    # created_at lets evidence-less candidates age into 'dormant' instead of
    # staying immortally 'hot'. created_at is always set (see age_days above).
    evidence_ref = candidate.last_evidence_at or candidate.created_at
    last_pp_age = (latest.scored_at - evidence_ref).days
```

- [ ] **Step 4: Run the new test + the full lifecycle suite to verify pass**

Run (user executes): `uv run pytest tests/pipeline/test_lifecycle.py -v`
Expected: all PASS, including the unchanged `test_derive_handles_null_last_evidence_at` (age 5 → last_pp_age 5 < 14 → still "emerging").

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/lifecycle.py tests/pipeline/test_lifecycle.py
git commit -m "fix(lifecycle): age evidence-less candidates from created_at, not 'today'

A None last_evidence_at was treated as last_pp_age=0, so candidates that never
received Stage-3 evidence could never go dormant and stayed 'hot' forever. Fall
back to created_at so they age into 'dormant' after 14 evidence-less days.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Full-suite regression check

**Files:** none modified.

**Interfaces:** none.

- [ ] **Step 1: Run the pipeline + config + scoring suites**

Run (user executes): `uv run pytest tests/pipeline tests/scoring tests/test_config.py -v`
Expected: all PASS. If anything else changed behaviour (e.g. an integration test asserting the old 0.82 default or old lifecycle behaviour), fix the assertion to the new calibrated values — do **not** revert the fixes.

- [ ] **Step 2: Run the full suite**

Run (user executes): `uv run pytest -q`
Expected: green. Paste failures back for triage before deploying.

---

## Phase 2 — Backfill consolidation (GATED on one more measurement; NOT in this plan)

**Why deferred:** The existing 167 candidates are fragmented duplicates that won't auto-merge, because `recluster.py:44`'s `merge_threshold=0.88` is *also* a nomic-era number and is almost certainly unreachable in e5 space — so the weekly merge pass fires ~never. Lowering it would consolidate the duplicate themes (e.g. ids 237/239/236/242, all dev/AI/no-code/automation). **But I have no measurement of candidate-centroid pairwise similarity**, so picking a value now would be a guess. This plan refuses to fabricate it.

**Gate:** Extend `scripts/diagnose_attachment.py` with a section that computes pairwise cosine between active-candidate centroids (within the single e5 bucket) and prints the distribution + the similarity of the visibly-duplicate top pairs. Run it against prod. Then a fast follow-up (its own short plan) will:
1. Add `recluster_merge_threshold` to `app/config.py`, defaulting to the measured value, and thread it into `run_weekly_recluster(merge_threshold=...)`.
2. Optionally trigger a one-off `run_weekly_recluster` pass over prod to collapse the backlog.

**Note on Tasks 1+2 sufficiency:** Even without Phase 2, the two fixes here un-stick the digest going forward — new evidence flows into existing candidates (Task 1) and stale evidence-less leaders age out of "hot" (Task 2). Phase 2 only accelerates de-duplication of the historical backlog. PR2 (drop the repo_count=0 → 30 validation floor) and PR3 (cohesion gate) remain separate de-biasing/hygiene follow-ups after Phase 2.

---

## Self-Review

- **Spec coverage:** Threshold recalibration → Task 1. Lifecycle immortality → Task 2. Regression safety → Task 3. Backlog de-fragmentation → explicitly deferred to Phase 2 with a stated gate (no fabricated number). `input_type:"query"` was investigated and downgraded to non-bug (both sides embed as "query", so consistent) — correctly excluded.
- **Placeholder scan:** No TBDs. Every code step shows full before/after. The one intentionally-absent value (Phase 2 merge threshold) is gated behind a measurement, not left as a placeholder in an executable task.
- **Type consistency:** `derive_lifecycle_state` / `run_identity_resolution` signatures unchanged. `evidence_ref` is `datetime`; `created_at` confirmed type-compatible via existing `age_days` subtraction. `get_settings.cache_clear()` used before asserting the cached default.
