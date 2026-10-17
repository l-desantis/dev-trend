# Validation PR 2 — Reclassify `repo_count = 0` as no-signal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop rewarding candidates with `repo_count = 0` validation snapshots a 30/100 "novel" score. An empty GitHub result more often means the search missed something than that the idea is genuinely unexplored — so treat it as no-signal: drop the validation dimension from the weighted total for that candidate and renormalize the remaining four dimensions to sum to 1.0. A candidate with no GitHub signal is then neither rewarded nor penalised on that axis.

**Architecture:** Two surgical changes. (1) `app/scoring/dimensions.py` — `validation_curve(repo_count, max_stars)` returns `None` when `repo_count == 0` instead of `30.0`. Return type widens from `float` to `float | None`. (2) `app/scoring/candidate_scorer.py` — when `val_score is None`, compute the weighted total over the remaining four dimensions and divide by `1.0 - WEIGHTS["validation"]` so the dropped dimension's weight redistributes proportionally. The `score_breakdown_json` records `"validation": null` for visibility.

**Context — what comes before/after this PR:** PR 1 (separate plan, already merged before starting this PR) rewrote GitHub keyword extraction and replaced the AND-of-5 query with multi-query union, so `repo_count = 0` snapshots are now far rarer and more trustworthy when they do occur. PR 3 (separate plan, after this one) adds an opt-in cohesion gate at stage 4. Per-pass recluster stays deferred to Phase 2.

**Prerequisite:** PR 1 should be merged and observed in production first. After PR 1, spot-check that validation snapshots show realistic `repo_count` values for candidates that previously showed 0 — that observation is the gate to starting this PR. If `repo_count` is still 0 across the board, PR 1 isn't doing its job and changing scoring math now would mask that.

**Tech Stack:** Python 3.11+, SQLAlchemy 2 async, pytest-asyncio, pydantic-settings.

**Environment note:** This project cannot execute `uv`/`pytest`/`python` directly in the agent shell (see CLAUDE.md). Every command shown as `! uv run pytest ...` must be handed to the user to run; wait for them to paste output before proceeding.

---

## File Structure

**Modified files:**
- `app/scoring/dimensions.py:91-99` — `validation_curve` returns `float | None`.
- `app/scoring/candidate_scorer.py:85-111` — per-candidate scoring block handles `val_score is None` and renormalizes.

**Modified test files:**
- `tests/scoring/test_dimensions.py` — update existing zero-repo test, add a new sanity test for nonzero cases.
- `tests/scoring/test_candidate_scorer.py` — add deterministic renormalization test + a companion sanity test that pins the non-renormalized path.

**No new files.**

**Known module weights** (referenced by the tests; see `app/scoring/candidate_scorer.py:24-30`):

```python
WEIGHTS = {
    "frequency": 0.25,
    "momentum": 0.30,
    "source_diversity": 0.15,
    "validation": 0.20,
    "specificity": 0.10,
}
```

---

## Task 1: `validation_curve` returns `None` for zero repos

**Files:**
- Modify: `app/scoring/dimensions.py:91-99`
- Test: `tests/scoring/test_dimensions.py` (update existing zero-repo test, add new tests)

- [ ] **Step 1.1: Update the dimensions tests**

Read `tests/scoring/test_dimensions.py` first. Find the existing test that asserts `validation_curve(0, ...) == 30.0` and change the assertion to expect `None`. Then add new tests:

```python
def test_validation_curve_returns_none_for_zero_repos() -> None:
    from app.scoring.dimensions import validation_curve
    assert validation_curve(0, 0) is None
    assert validation_curve(0, 9999) is None


def test_validation_curve_still_returns_score_for_nonzero() -> None:
    from app.scoring.dimensions import validation_curve
    assert validation_curve(3, 1000) == 90.0
    assert validation_curve(10, 10_000) == 70.0
    assert validation_curve(100, 100_000) == 30.0
```

- [ ] **Step 1.2: Run the dimensions tests to verify they fail**

Ask the user to run:

```
! uv run pytest tests/scoring/test_dimensions.py -v
```

Expected: the two new/updated tests fail (current code returns `30.0` for `repo_count == 0`).

- [ ] **Step 1.3: Update `validation_curve`**

Edit `app/scoring/dimensions.py:91-99`. Replace the existing function:

```python
def validation_curve(repo_count: int, max_stars: int) -> float | None:
    """Non-monotonic validation score per spec §5.2. Returns 0–100 directly.

    Returns ``None`` when ``repo_count == 0``: an empty GitHub result is treated
    as no signal (likely a search miss, not a verified novel idea) and the
    candidate_scorer renormalizes the remaining dimensions in response.
    """
    if repo_count == 0:
        return None
    if repo_count <= 5 and max_stars <= 5_000:
        return 90.0
    if repo_count <= 20 and max_stars <= 20_000:
        return 70.0
    return 30.0
```

- [ ] **Step 1.4: Run the dimensions tests to verify they pass**

Ask the user to run:

```
! uv run pytest tests/scoring/test_dimensions.py -v
```

Expected: all pass.

---

## Task 2: `candidate_scorer` renormalizes when validation is `None`

**Files:**
- Modify: `app/scoring/candidate_scorer.py:85-111`
- Test: `tests/scoring/test_candidate_scorer.py` (append two new tests)

- [ ] **Step 2.1: Write the deterministic renormalization test**

Append to `tests/scoring/test_candidate_scorer.py` (read the file first to copy fixture/import style — `session: AsyncSession` is the standard fixture name). All four input scores are controlled by monkeypatching `normalize_with_neutral_fallback` and the three raw functions, so we can compute the exact expected total from first principles:

```python
async def test_score_renormalizes_when_validation_is_none(
    session: AsyncSession, monkeypatch
) -> None:
    from unittest.mock import AsyncMock
    import pytest
    from app.models import OpportunityCandidate
    from app.scoring.candidate_scorer import score_all_candidates, WEIGHTS
    import app.scoring.candidate_scorer as cs

    # Single candidate with no CandidateValidation row → repo_count=0
    # → validation_curve returns None.
    c = OpportunityCandidate(
        problem_statement="x", audience="y", specificity=5,
    )
    session.add(c)
    await session.commit()

    # Force every raw-input function and the percentile normaliser to known
    # deterministic outputs so we can compute the exact expected total.
    monkeypatch.setattr(cs, "frequency_raw", AsyncMock(return_value=10.0))
    monkeypatch.setattr(cs, "momentum_raw", AsyncMock(return_value=5.0))
    monkeypatch.setattr(cs, "source_diversity_raw", AsyncMock(return_value=3.0))
    monkeypatch.setattr(
        cs, "normalize_with_neutral_fallback",
        lambda raws: {k: 50.0 for k in raws},
    )

    rows = await score_all_candidates(session)

    assert len(rows) == 1
    breakdown = rows[0].score_breakdown_json
    # Validation breakdown entry must be present and explicitly None.
    assert breakdown["validation"] is None

    # First principles: with frequency=momentum=diversity=50.0 (forced
    # neutral normalisation) and specificity_raw = candidate.specificity * 20
    # = 5*20 = 100.0, validation dimension dropped. The renormalized total
    # is the non-validation weighted sum divided by the remaining weight mass.
    spec_raw = 100.0
    weighted_no_val = (
        50.0 * WEIGHTS["frequency"]
        + 50.0 * WEIGHTS["momentum"]
        + 50.0 * WEIGHTS["source_diversity"]
        + spec_raw * WEIGHTS["specificity"]
    )
    expected = weighted_no_val / (1.0 - WEIGHTS["validation"])
    # With the WEIGHTS recorded at the top of this plan, expected = 56.25.
    assert rows[0].score_total == pytest.approx(expected, abs=1e-6)
```

- [ ] **Step 2.2: Write the companion sanity test for the non-renormalized path**

Append to the same test file:

```python
async def test_score_uses_validation_when_present(
    session: AsyncSession, monkeypatch
) -> None:
    """Sanity check the *non*-renormalized path stays at the original total."""
    from unittest.mock import AsyncMock
    from datetime import UTC, datetime
    import pytest
    from app.models import CandidateValidation, OpportunityCandidate
    from app.scoring.candidate_scorer import score_all_candidates, WEIGHTS
    import app.scoring.candidate_scorer as cs

    c = OpportunityCandidate(
        problem_statement="x", audience="y", specificity=5,
    )
    session.add(c)
    await session.flush()
    session.add(CandidateValidation(
        candidate_id=c.id,
        signal_type="composite",
        signal_value=3.0,
        validated_at=datetime.now(UTC),
        metadata_json={"repo_count": 3, "max_stars": 1000},  # → val_score 90
    ))
    await session.commit()

    monkeypatch.setattr(cs, "frequency_raw", AsyncMock(return_value=10.0))
    monkeypatch.setattr(cs, "momentum_raw", AsyncMock(return_value=5.0))
    monkeypatch.setattr(cs, "source_diversity_raw", AsyncMock(return_value=3.0))
    monkeypatch.setattr(
        cs, "normalize_with_neutral_fallback",
        lambda raws: {k: 50.0 for k in raws},
    )

    rows = await score_all_candidates(session)
    assert rows[0].score_breakdown_json["validation"] == 90.0
    expected = (
        50.0 * WEIGHTS["frequency"]
        + 50.0 * WEIGHTS["momentum"]
        + 50.0 * WEIGHTS["source_diversity"]
        + 90.0 * WEIGHTS["validation"]
        + 100.0 * WEIGHTS["specificity"]
    )
    # With current WEIGHTS this is 12.5 + 15 + 7.5 + 18 + 10 = 63.0
    assert rows[0].score_total == pytest.approx(expected, abs=1e-6)
```

- [ ] **Step 2.3: Run the new tests to verify they fail**

Ask the user to run:

```
! uv run pytest tests/scoring/test_candidate_scorer.py::test_score_renormalizes_when_validation_is_none tests/scoring/test_candidate_scorer.py::test_score_uses_validation_when_present -v
```

Expected: the renormalization test fails — current scorer multiplies `None * WEIGHTS["validation"]` and raises `TypeError`. The validation-present sanity test should already pass against the current code (it pins existing behaviour).

- [ ] **Step 2.4: Implement renormalization in `candidate_scorer.py`**

Edit `app/scoring/candidate_scorer.py:85-111`. Replace the per-candidate scoring block:

```python
    for c in candidates:
        val_row = await get_latest_validation(session, c.id)
        if val_row and val_row.metadata_json:
            repo_count = int(val_row.metadata_json.get("repo_count", 0))
            max_stars = int(val_row.metadata_json.get("max_stars", 0))
        else:
            repo_count, max_stars = 0, 0

        val_score = validation_curve(repo_count, max_stars)  # may be None
        spec_score = specificity_raw(c)

        breakdown = {
            "frequency": {"raw": freq_raws[c.id], "score": freq_scores[c.id]},
            "momentum": {"raw": mom_raws[c.id], "score": mom_scores[c.id]},
            "source_diversity": {"raw": div_raws[c.id], "score": div_scores[c.id]},
            "validation": val_score,
            "specificity": spec_score,
            "weights": WEIGHTS,
        }

        weighted = (
            freq_scores[c.id] * WEIGHTS["frequency"]
            + mom_scores[c.id] * WEIGHTS["momentum"]
            + div_scores[c.id] * WEIGHTS["source_diversity"]
            + spec_score * WEIGHTS["specificity"]
        )
        if val_score is None:
            # Drop the validation dimension and rescale remaining weights to sum to 1.0
            remaining = 1.0 - WEIGHTS["validation"]
            total = weighted / remaining if remaining > 0 else 0.0
        else:
            total = weighted + val_score * WEIGHTS["validation"]
```

- [ ] **Step 2.5: Run all scoring tests**

Ask the user to run:

```
! uv run pytest tests/scoring/ -v
```

Expected: all pass. If any existing test asserted a specific total that assumed `validation=30` for `repo_count=0`, update it to the new renormalized total or seed a non-empty validation row.

---

## Final verification

- [ ] **Run the full suite for regressions**

Ask the user to run:

```
! uv run pytest -q
```

Expected: green across the whole repo.

- [ ] **Commit and open PR**

```bash
git add app/scoring/dimensions.py app/scoring/candidate_scorer.py tests/scoring/test_dimensions.py tests/scoring/test_candidate_scorer.py
git commit -m "fix(scoring): treat repo_count=0 as no-signal, renormalize total

validation_curve now returns None for zero-repo results (which usually
mean 'we couldn't find competitors' rather than 'this is novel').
candidate_scorer drops the validation dimension in that case and rescales
the remaining four weights to sum to 1.0, so a candidate with no GitHub
signal is neither rewarded nor penalised on that axis.

Renormalization is locked in with a deterministic test that monkeypatches
normalize_with_neutral_fallback and the raw-input functions to known
values, then asserts the exact expected total (56.25 with current WEIGHTS),
plus a companion test pinning the non-renormalized path to 63.0."
```

Open the PR. After merge, spot-check a few candidates with the new breakdown: those with `repo_count = 0` should show `validation: null` in `score_breakdown_json` and a total that doesn't include the old 30/100 reward.

---

## Self-review notes

- Type signature change `float → float | None` is contained: the single consumer is `candidate_scorer.py` and it's updated in the same PR. Worth a `grep -rn "validation_curve" app/` during review to be sure nothing else imports it.
- Both deterministic tests pin a real number (56.25 and 63.0). If `WEIGHTS` changes later, both expected values move predictably — the tests will fail loudly, which is the point.
- All test commands routed through `! uv run pytest` per CLAUDE.md.
