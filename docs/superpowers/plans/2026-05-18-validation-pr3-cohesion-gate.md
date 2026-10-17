# Validation PR 3 — Stage-4 cohesion gate (logging-only by default)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catch incoherent clusters at stage 4 — the upstream root cause of bad candidates like "ADHD task apps + vintage music gear + P2P trade trust" merged under one vague problem statement. Add a cohesion check (mean cosine similarity of pain-point embeddings to centroid) immediately after HDBSCAN/Agglomerative labels are assigned. Ship the gate **disabled** initially: cohesion is logged for every cluster so the natural distribution can be observed in production, then the threshold is enabled via env var without a code change.

**Architecture:** Two minimal additions to `app/pipeline/clustering.py`: a `_mean_cosine_to_centroid` helper and a per-cluster cohesion check inside `run_clustering`'s cluster loop. Every cluster emits a `clustering_cohesion` structlog event regardless of threshold; clusters fail the gate (incrementing `report.low_cohesion_rejected`, leaving pain-points unattached) only when the new `min_cohesion` parameter is `> 0`. A new `clustering_min_cohesion` setting (default `0.0`) is plumbed through `app/config.py` and `app/pipeline/orchestrator.py`. Because the default is `0.0`, day-one behaviour is unchanged except for the new log events — the operator raises the threshold via `CLUSTERING_MIN_COHESION` env var after observing real cohesion data.

**Context — what comes before/after this PR:** PR 1 (separate plan, merged) fixed GitHub search keyword extraction + multi-query. PR 2 (separate plan, merged) reclassified `repo_count == 0` as no-signal in scoring. This PR is the last in the validation-pipeline-fixes series. Per-pass recluster remains deferred to Phase 2 and is not in scope here.

**Prerequisite:** PRs 1 and 2 should be merged and observed first. They address the *downstream* validation/scoring errors that made the bad candidate look "validated." This PR addresses the *upstream* clustering hygiene that produced the bad candidate. Cleaner to fix downstream and confirm the visible symptom is gone before introducing an opt-in gate that changes ingestion behaviour.

**Tech Stack:** Python 3.11+, SQLAlchemy 2 async, numpy, structlog, pydantic-settings.

**Environment note:** This project cannot execute `uv`/`pytest`/`python` directly in the agent shell (see CLAUDE.md). Every command shown as `! uv run pytest ...` must be handed to the user to run; wait for them to paste output before proceeding.

---

## File Structure

**Modified files:**
- `app/config.py:155-157` — add `clustering_min_cohesion: float = 0.0`.
- `app/pipeline/clustering.py` — add `_mean_cosine_to_centroid` helper, extend `ClusteringReport`, add `min_cohesion` parameter to `run_clustering`, log cohesion per cluster, opt-in reject.
- `app/pipeline/orchestrator.py:66-70` — pass `settings.clustering_min_cohesion` into `run_clustering`.

**Modified test files:**
- `tests/pipeline/test_clustering.py` — three new tests: logging-only, opt-in rejection, opt-in acceptance.

**No new files.**

**Rollout strategy:** This PR ships the gate **disabled** (`clustering_min_cohesion = 0.0`). Every cluster's cohesion is logged regardless. After ~1 week of production logs, plot the distribution of `clustering_cohesion` events and pick a threshold near the 5th–10th percentile of accepted clusters' cohesion (or wherever the bad clusters cluster). Then set `CLUSTERING_MIN_COHESION` via env var — no code change required.

---

## Task 1: Config setting (disabled by default)

**Files:**
- Modify: `app/config.py:155-157`

- [ ] **Step 1.1: Add the setting**

Edit `app/config.py`. Insert after `clustering_min_cluster_size`:

```python
    clustering_min_cluster_size: int = 3
    # Gate disabled by default (0.0 = accept everything). Cohesion is still
    # logged for every cluster so operators can observe the natural
    # distribution and raise this threshold once a safe value is known.
    clustering_min_cohesion: float = 0.0
    specificity_gate: int = 2
```

(No test for this single-line config addition — the gate behaviour is tested via `run_clustering` directly in Task 2.)

---

## Task 2: Cohesion logging + opt-in gate in `run_clustering`

**Files:**
- Modify: `app/pipeline/clustering.py` (new helper, extend `ClusteringReport`, new param + logic in `run_clustering`)
- Test: `tests/pipeline/test_clustering.py` (append three new tests)

- [ ] **Step 2.1: Write the failing tests**

Append to `tests/pipeline/test_clustering.py`. The tests reuse the file's existing `_seed_item`, `_make_pp`, and `_unit` helpers — re-read the file before writing to confirm those names match.

```python
async def test_clustering_logs_cohesion_when_gate_disabled(
    session: AsyncSession, monkeypatch
) -> None:
    base = _unit([1.0, 0.0] + [0.0] * 30)
    for i in range(3):
        item = await _seed_item(session, f"lg{i}")
        noise = np.random.default_rng(i).normal(0, 0.01, 32)
        vec = _unit((np.array(base) + noise).tolist())
        await _make_pp(session, item.id, vec)
    await session.commit()

    import app.pipeline.clustering as clustering_mod
    monkeypatch.setattr(
        clustering_mod, "_cluster_labels",
        lambda matrix, min_cluster_size: np.array([0, 0, 0], dtype=int),
    )

    # Capture structlog events emitted by the clustering module.
    events: list[tuple[str, dict]] = []

    class RecordingLogger:
        def info(self, event, **kw): events.append((event, kw))
        def warning(self, event, **kw): events.append((event, kw))
        def error(self, event, **kw): events.append((event, kw))
        def debug(self, event, **kw): events.append((event, kw))

    monkeypatch.setattr(clustering_mod, "log", RecordingLogger())

    report = await run_clustering(
        session, min_cluster_size=2, min_cohesion=0.0,
    )

    # Gate disabled: cluster is still created.
    assert report.candidates_created == 1
    assert report.low_cohesion_rejected == 0
    # But cohesion was logged for observability.
    cohesion_events = [kw for ev, kw in events if ev == "clustering_cohesion"]
    assert len(cohesion_events) == 1
    assert 0.0 <= cohesion_events[0]["cohesion"] <= 1.0


async def test_clustering_rejects_low_cohesion_when_gate_enabled(
    session: AsyncSession, monkeypatch
) -> None:
    # Three pain-points whose embeddings are geometrically dispersed:
    # mean cosine to centroid will be well below 0.30.
    e1 = _unit([1.0, 0.0, 0.0] + [0.0] * 29)
    e2 = _unit([0.0, 1.0, 0.0] + [0.0] * 29)
    e3 = _unit([0.0, 0.0, 1.0] + [0.0] * 29)

    for i, v in enumerate([e1, e2, e3]):
        item = await _seed_item(session, f"lc{i}")
        await _make_pp(session, item.id, v)
    await session.commit()

    import app.pipeline.clustering as clustering_mod
    monkeypatch.setattr(
        clustering_mod, "_cluster_labels",
        lambda matrix, min_cluster_size: np.array([0, 0, 0], dtype=int),
    )

    report = await run_clustering(
        session, min_cluster_size=2, min_cohesion=0.30,
    )

    assert report.candidates_created == 0
    assert report.low_cohesion_rejected == 1
    pps = (await session.execute(select(PainPoint))).scalars().all()
    assert all(pp.candidate_id is None for pp in pps)


async def test_clustering_accepts_high_cohesion_when_gate_enabled(
    session: AsyncSession, monkeypatch
) -> None:
    base = _unit([1.0, 0.0] + [0.0] * 30)
    for i in range(3):
        item = await _seed_item(session, f"hc{i}")
        noise = np.random.default_rng(i).normal(0, 0.01, 32)
        vec = _unit((np.array(base) + noise).tolist())
        await _make_pp(session, item.id, vec)
    await session.commit()

    import app.pipeline.clustering as clustering_mod
    monkeypatch.setattr(
        clustering_mod, "_cluster_labels",
        lambda matrix, min_cluster_size: np.array([0, 0, 0], dtype=int),
    )

    report = await run_clustering(
        session, min_cluster_size=2, min_cohesion=0.30,
    )

    assert report.candidates_created == 1
    assert report.low_cohesion_rejected == 0
```

- [ ] **Step 2.2: Run the new tests to verify they fail**

Ask the user to run:

```
! uv run pytest tests/pipeline/test_clustering.py::test_clustering_logs_cohesion_when_gate_disabled tests/pipeline/test_clustering.py::test_clustering_rejects_low_cohesion_when_gate_enabled tests/pipeline/test_clustering.py::test_clustering_accepts_high_cohesion_when_gate_enabled -v
```

Expected: FAIL — `run_clustering()` doesn't accept `min_cohesion` yet, and `low_cohesion_rejected` is not on `ClusteringReport`.

- [ ] **Step 2.3: Extend `ClusteringReport`**

Edit `app/pipeline/clustering.py`. Update the dataclass:

```python
@dataclass
class ClusteringReport:
    unattached: int = 0
    candidates_created: int = 0
    noise_points: int = 0
    low_cohesion_rejected: int = 0
    duration_ms: int = 0
```

- [ ] **Step 2.4: Add the `_mean_cosine_to_centroid` helper**

Add just above `run_clustering`:

```python
def _mean_cosine_to_centroid(vecs: np.ndarray, centroid: np.ndarray) -> float:
    norms = np.linalg.norm(vecs, axis=1)
    c_norm = np.linalg.norm(centroid)
    if c_norm == 0:
        return 0.0
    safe_norms = np.where(norms == 0, 1.0, norms)
    sims = (vecs @ centroid) / (safe_norms * c_norm)
    sims = np.where(norms == 0, 0.0, sims)
    return float(sims.mean())
```

- [ ] **Step 2.5: Change the `run_clustering` signature**

Update the function signature and docstring:

```python
async def run_clustering(
    session: AsyncSession,
    *,
    min_cluster_size: int,
    min_cohesion: float = 0.0,
) -> ClusteringReport:
    """Stage 4: cluster unattached PainPoints and create new OpportunityCandidates.

    Computes mean cosine similarity of each cluster's pain-points to the
    cluster centroid and logs it. When ``min_cohesion > 0``, clusters whose
    cohesion is below the threshold are rejected (pain-points left
    unattached, treated as noise). Defaults to 0.0 (gate disabled,
    logging-only) so the natural cohesion distribution can be observed
    before enabling the threshold.
    """
```

- [ ] **Step 2.6: Insert the per-cluster cohesion log + opt-in gate**

Inside the per-cluster `for lbl in unique_labels:` loop, after the existing `mixed` embedding-model check (and the `continue` that handles it) but BEFORE the `OpportunityCandidate(...)` construction, insert:

```python
        cohesion = _mean_cosine_to_centroid(cluster_vecs, mean_vec)
        log.info(
            "clustering_cohesion",
            label=int(lbl),
            cohesion=round(cohesion, 4),
            size=len(cluster_pps),
            embedding_model=model_name,
        )
        if min_cohesion > 0 and cohesion < min_cohesion:
            log.info(
                "clustering_low_cohesion_rejected",
                label=int(lbl),
                cohesion=round(cohesion, 4),
                threshold=min_cohesion,
                size=len(cluster_pps),
            )
            report.low_cohesion_rejected += 1
            continue
```

- [ ] **Step 2.7: Run the new tests to verify they pass**

Ask the user to run:

```
! uv run pytest tests/pipeline/test_clustering.py -v
```

Expected: all pass (three new + five pre-existing).

---

## Task 3: Wire the setting into the orchestrator

**Files:**
- Modify: `app/pipeline/orchestrator.py:66-70`

- [ ] **Step 3.1: Pass `min_cohesion` from settings**

Edit `app/pipeline/orchestrator.py:66-70`:

```python
    async with session_factory() as session:
        report.clustering = await run_clustering(
            session,
            min_cluster_size=settings.clustering_min_cluster_size,
            min_cohesion=settings.clustering_min_cohesion,
        )
```

- [ ] **Step 3.2: Run orchestrator + clustering tests for regressions**

Ask the user to run:

```
! uv run pytest tests/pipeline/test_orchestrator.py tests/pipeline/test_clustering.py -v
```

Expected: all pass. Pre-existing orchestrator tests should be unaffected because the default `clustering_min_cohesion = 0.0` makes the gate a no-op.

---

## Final verification

- [ ] **Run the full suite**

Ask the user to run:

```
! uv run pytest -q
```

Expected: green across the whole repo.

- [ ] **Commit and open PR**

```bash
git add app/config.py app/pipeline/clustering.py app/pipeline/orchestrator.py tests/pipeline/test_clustering.py
git commit -m "feat(clustering): log cohesion + opt-in cohesion gate at stage 4

Computes mean cosine similarity of each cluster's pain-points to its
centroid and logs it (clustering_cohesion event) for every cluster.
Adds clustering_min_cohesion setting which, when > 0, rejects clusters
below the threshold before they become OpportunityCandidates. Ships
DISABLED (default 0.0) so operators can observe the natural cohesion
distribution from production logs before enabling the gate."
```

Open the PR.

**Post-deploy follow-up (not part of this plan):** after ~1 week of production logs, plot the distribution of `clustering_cohesion` events. Pick a threshold near the 5th–10th percentile of accepted clusters' cohesion (or wherever the bad clusters cluster), then set `CLUSTERING_MIN_COHESION` via env var. No code change required.

---

## Phase 2 (deferred): Per-pass recluster

**Status:** Not in this plan. Only revisit if PRs 1–3 don't sufficiently reduce bad-candidate rates after a few weeks in production with the cohesion gate enabled.

**Why deferred:**
- `run_weekly_recluster` loads N days of pain-points and does O(C²) candidate-pair centroid comparisons, where C is candidate count. Cost grows with the catalog.
- The original failure mode (incoherent merger of unrelated topics) is addressed at source by this PR's cohesion gate once the threshold is enabled. Drift between passes is hypothesised, not measured.
- Operationally heavier than any change in PRs 1–3; risk of slowing every pipeline pass should be justified by data, not anticipation.

**If revisited, the rough shape would be:**
- Add `recluster_per_pass_enabled: bool = False` and `recluster_per_pass_window_days: int = 7` to `app/config.py`.
- After labelling in `app/pipeline/orchestrator.py`, call `run_weekly_recluster(session, window_days=settings.recluster_per_pass_window_days)` when the flag is set.
- Test with `monkeypatch.setattr(orch, "run_weekly_recluster", fake)` to assert the call is/isn't made depending on the flag.

Before implementing, gather production data on:
- How often `clustering_low_cohesion_rejected` fires (this PR, once threshold raised)
- How many bad candidates still slip through to validation
- p95 duration of the existing weekly recluster pass (to estimate per-pass cost)

---

## Self-review notes

- `clustering_min_cohesion = 0.0` default makes this PR a behavioural no-op on day one — the only externally observable change is new `clustering_cohesion` log events. Safe to ship without coordinating threshold tuning.
- The cohesion check sits AFTER the existing `mixed`-embedding-model check, so a mixed-model cluster is still rejected with its existing error log and never reaches the cohesion calculation.
- `_mean_cosine_to_centroid` handles zero-norm vectors defensively (`np.where(norms == 0, ...)`) so a degenerate embedding doesn't divide-by-zero.
- All test commands routed through `! uv run pytest` per CLAUDE.md.
