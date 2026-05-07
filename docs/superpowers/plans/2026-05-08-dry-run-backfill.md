# Backfill Dry-Run Token Estimator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL — use `superpowers:executing-plans` (or `superpowers:subagent-driven-development`) to work through this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

## Context

Today, running `scripts/run_backfill.py` with a billed LLM provider (NIM) spends an unknown number of tokens — operators have no way to predict cost before kicking off a real run. This plan adds a `--dry-run` flag that:

1. Runs the ingestion phase normally (no LLM tokens — just HTTP fetches).
2. Skips the pipeline (extraction + labelling) and instead **counts** what *would* be sent to the LLM and prints a token estimate.
3. Exits without mutating any pain-point / candidate state.

The estimate uses a char-based heuristic (`chars / 4`) so we don't add a tokenizer dependency. Documented as ±20% accuracy. The operator can use the number to decide whether to run for real, lower `--max-extraction-items`, or switch providers.

**Cost surface (from exploration):**
- **Extract** dominates: 1 LLM call per pending `SourceItem` with `role='extraction'`. Prompt = `EXTRACT_SYSTEM_PROMPT` + `EXTRACT_USER_PROMPT.format(text=truncate(title+body, 4000))`. Output ≈ 100 tokens of JSON.
- **Labelling**: 1 call per `OpportunityCandidate` with `labeller_model IS NULL`. Smaller volume, smaller prompt.
- **Embedding**: only billed when `embedding_provider="nim"`. 1 batched call across new pain points.

**Goal:** Add `--dry-run` to `scripts/run_backfill.py` that runs ingestion, then prints a per-stage + total token estimate, without invoking any LLM.

**Architecture:** New pure module `app/pipeline/token_estimator.py` exposes `estimate_tokens(session_factory, settings)` returning a `TokenEstimate` dataclass. `bulk_backfill()` gains a `dry_run: bool = False` param: when `True`, after ingestion it calls the estimator instead of `run_pipeline()`. CLI gains `--dry-run` and a Rich-formatted report printer.

**Tech Stack:** Python 3.11+, async SQLAlchemy, structlog, Rich, pytest. No new deps.

**Environment note:** Project uses `uv` on WSL2. Per the project's `CLAUDE.md`, the assistant cannot run `uv` / `python` / `pytest` directly — at every "run …" step, **ask the operator to run the command and paste output**. Wait for output before continuing.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `app/pipeline/token_estimator.py` | Create | Pure module: `chars_to_tokens()`, `TokenEstimate` dataclass, `estimate_tokens()` async function. |
| `app/ingestion/backfill.py` | Modify (lines 38–143) | Add `dry_run: bool = False` param to `bulk_backfill`; add optional `estimate: TokenEstimate \| None` field to `BackfillReport`; branch on `dry_run` after ingestion. |
| `scripts/run_backfill.py` | Modify (lines 128–316) | Add `--dry-run` argparse flag; pass through to `bulk_backfill`; if dry-run, print a Rich-formatted estimate report instead of the normal summary. |
| `tests/test_token_estimator.py` | Create | Unit tests for `chars_to_tokens()` and `estimate_tokens()` against in-memory SQLite. |
| `tests/test_run_backfill_cli.py` | Modify | Add CLI smoke test that `--dry-run` ingests but does NOT create pain points. |

---

## Task 1: Token-counting helpers + `TokenEstimate` dataclass (TDD)

**Files:**
- Create: `app/pipeline/token_estimator.py`
- Test: `tests/test_token_estimator.py`

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_token_estimator.py
"""Unit tests for the dry-run token estimator."""
from app.pipeline.token_estimator import (
    TokenEstimate,
    StageEstimate,
    chars_to_tokens,
    extract_prompt_chars,
    label_prompt_chars,
)


def test_chars_to_tokens_uses_div_4_heuristic():
    assert chars_to_tokens(0) == 0
    assert chars_to_tokens(4) == 1
    assert chars_to_tokens(400) == 100
    # Rounds up so we never under-quote.
    assert chars_to_tokens(5) == 2


def test_extract_prompt_chars_includes_system_and_user_template():
    """A short input text yields prompt_chars > template fixed cost."""
    short = extract_prompt_chars("hello")
    longer = extract_prompt_chars("hello world " * 50)
    assert longer > short
    # Sanity: the EXTRACT_USER_PROMPT template alone is ~420 chars.
    assert short > 400


def test_extract_prompt_chars_truncates_at_4000():
    """Body is capped at 4000 chars (matches extract.py:79)."""
    huge = "x" * 10_000
    assert extract_prompt_chars(huge) == extract_prompt_chars("x" * 4_000)


def test_label_prompt_chars_grows_with_evidence():
    a = label_prompt_chars(evidence_count=3, avg_evidence_chars=80, category_count=10)
    b = label_prompt_chars(evidence_count=10, avg_evidence_chars=80, category_count=10)
    assert b > a


def test_token_estimate_total_sums_stages():
    est = TokenEstimate(
        extract=StageEstimate(calls=10, input_tokens=1000, output_tokens=200),
        label=StageEstimate(calls=2, input_tokens=300, output_tokens=100),
        embed=StageEstimate(calls=1, input_tokens=500, output_tokens=0),
    )
    assert est.total_tokens == 1000 + 200 + 300 + 100 + 500
```

- [ ] **Step 2: Ask the operator to run the tests and confirm they FAIL with `ModuleNotFoundError`.**

Operator command:

```bash
uv run pytest tests/test_token_estimator.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.pipeline.token_estimator'`. **Wait for paste-back before continuing.**

- [ ] **Step 3: Create `app/pipeline/token_estimator.py` with the helpers and dataclasses.**

```python
"""Dry-run token estimator for the backfill pipeline.

Char-based heuristic (chars / 4) — accurate to within ~20% for Llama/Qwen
tokenizers, and good enough to predict whether a real backfill will cost
$0.10 or $10. No tokenizer dependency.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.llm.prompts import (
    EXTRACT_SYSTEM_PROMPT,
    EXTRACT_USER_PROMPT,
    LABEL_CLUSTER_PROMPT,
)

# Output budgets per call (heuristic — observed JSON sizes).
_EXTRACT_OUTPUT_TOKENS = 100
_LABEL_OUTPUT_TOKENS = 200

# Heuristics for projecting NEW labelling work that depends on extraction output.
# Documented in the dry-run report so the operator knows these are not exact.
_PROJECTED_PAINPOINT_RATIO = 0.30   # 30% of extracted items become pain points
_PROJECTED_CLUSTER_SIZE = 5         # avg pain points per cluster


def chars_to_tokens(chars: int) -> int:
    """Rough token count using the 1-token-per-4-chars heuristic. Rounds up."""
    if chars <= 0:
        return 0
    return math.ceil(chars / 4)


def extract_prompt_chars(source_text: str) -> int:
    """Total prompt chars for one extract call (system + user, body capped at 4000)."""
    body = source_text[:4000]
    user = EXTRACT_USER_PROMPT.format(text=body)
    return len(EXTRACT_SYSTEM_PROMPT) + len(user)


def label_prompt_chars(
    *,
    evidence_count: int,
    avg_evidence_chars: int,
    category_count: int,
) -> int:
    """Total prompt chars for one label_cluster call."""
    evidence_lines = "\n".join(["- " + "x" * avg_evidence_chars] * evidence_count)
    categories = ", ".join(["category"] * category_count) or "(none)"
    rendered = LABEL_CLUSTER_PROMPT.format(
        evidence_lines=evidence_lines,
        categories=categories,
    )
    return len(rendered)


@dataclass
class StageEstimate:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TokenEstimate:
    extract: StageEstimate = field(default_factory=StageEstimate)
    label: StageEstimate = field(default_factory=StageEstimate)
    label_projected: StageEstimate = field(default_factory=StageEstimate)
    embed: StageEstimate = field(default_factory=StageEstimate)
    notes: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return (
            self.extract.total
            + self.label.total
            + self.label_projected.total
            + self.embed.total
        )

    def to_dict(self) -> dict:
        return {
            "extract": self.extract.__dict__,
            "label": self.label.__dict__,
            "label_projected": self.label_projected.__dict__,
            "embed": self.embed.__dict__,
            "total_tokens": self.total_tokens,
            "notes": list(self.notes),
        }
```

- [ ] **Step 4: Ask the operator to run the tests and confirm they PASS.**

Operator command: `uv run pytest tests/test_token_estimator.py -v`. Expected: 5 passed. **Wait for paste-back.**

- [ ] **Step 5: Commit.**

```bash
git add app/pipeline/token_estimator.py tests/test_token_estimator.py
git commit -m "feat(token-estimator): add char-based prompt-cost helpers"
```

---

## Task 2: `estimate_tokens()` async query function (TDD)

**Files:**
- Modify: `app/pipeline/token_estimator.py`
- Modify: `tests/test_token_estimator.py`

- [ ] **Step 1: Append the failing test that exercises a real DB.**

```python
# Append to tests/test_token_estimator.py
import pytest

from app.db import _get_session_factory, init_db, reset_engine
from app.config import get_settings
from app.models import OpportunityCandidate, SourceItem
from app.pipeline.token_estimator import estimate_tokens


@pytest.mark.asyncio
async def test_estimate_tokens_counts_pending_extraction(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path/'est.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    get_settings.cache_clear()
    reset_engine()
    await init_db()

    factory = _get_session_factory()
    async with factory() as s:
        # 2 pending extraction items, 1 already extracted (excluded), 1 wrong role (excluded).
        s.add_all([
            SourceItem(source_type="hn", external_id="1", title="I wish there was X",
                       body="long body " * 20, role="extraction", extraction_state="pending"),
            SourceItem(source_type="hn", external_id="2", title="Need help with Y",
                       body="another", role="extraction", extraction_state="pending"),
            SourceItem(source_type="hn", external_id="3", title="done",
                       role="extraction", extraction_state="extracted"),
            SourceItem(source_type="hn", external_id="4", title="not extraction",
                       role="reference", extraction_state="pending"),
            # 1 unlabelled candidate.
            OpportunityCandidate(centroid_embedding=[0.0] * 32,
                                 embedding_model="mock", labeller_model=None),
            # 1 already-labelled candidate (excluded).
            OpportunityCandidate(centroid_embedding=[0.0] * 32,
                                 embedding_model="mock", labeller_model="mock:v1"),
        ])
        await s.commit()

    estimate = await estimate_tokens(factory, get_settings())

    assert estimate.extract.calls == 2
    assert estimate.extract.input_tokens > 0
    assert estimate.extract.output_tokens == 2 * 100  # _EXTRACT_OUTPUT_TOKENS
    assert estimate.label.calls == 1
    assert estimate.label.output_tokens == 1 * 200    # _LABEL_OUTPUT_TOKENS
    assert estimate.label_projected.calls > 0          # 30% of 2 / 5 ≈ small but >0
    assert estimate.total_tokens > 0
    assert any("heuristic" in n.lower() for n in estimate.notes)
```

> **Note for the implementer:** check the actual column names on `OpportunityCandidate` (especially the centroid field) before running — `app/models.py` is authoritative. If the field is named differently (e.g. `centroid` vs `centroid_embedding`), update the seed rows accordingly.

- [ ] **Step 2: Ask the operator to run the new test and confirm it FAILS with `ImportError: cannot import name 'estimate_tokens'`.**

`uv run pytest tests/test_token_estimator.py::test_estimate_tokens_counts_pending_extraction -v`. **Wait for paste-back.**

- [ ] **Step 3: Implement `estimate_tokens()`.**

Append to `app/pipeline/token_estimator.py`:

```python
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.models import OpportunityCandidate, SourceItem

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.config import Settings


async def estimate_tokens(
    session_factory: "async_sessionmaker",
    settings: "Settings",
) -> TokenEstimate:
    """Estimate tokens that the next bulk_backfill pipeline run would spend.

    Reads (does not mutate):
      - SourceItem rows with role='extraction' AND extraction_state='pending'
        → exact extract-stage cost
      - OpportunityCandidate rows with labeller_model IS NULL
        → exact (already-existing) label-stage cost
      - Heuristic projection for NEW clusters from this run's extractions.
    """
    estimate = TokenEstimate()

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(SourceItem.title, SourceItem.body)
                .where(SourceItem.role == "extraction")
                .where(SourceItem.extraction_state == "pending")
            )
        ).all()

        for title, body in rows:
            text = f"{title or ''}\n{body or ''}"
            estimate.extract.calls += 1
            estimate.extract.input_tokens += chars_to_tokens(extract_prompt_chars(text))
            estimate.extract.output_tokens += _EXTRACT_OUTPUT_TOKENS

        unlabelled = (
            await session.execute(
                select(func.count(OpportunityCandidate.id))
                .where(OpportunityCandidate.labeller_model.is_(None))
            )
        ).scalar_one()

    # Existing unlabelled candidates (definite cost).
    if unlabelled:
        per_call_in = chars_to_tokens(
            label_prompt_chars(evidence_count=10, avg_evidence_chars=80, category_count=10)
        )
        estimate.label.calls = unlabelled
        estimate.label.input_tokens = unlabelled * per_call_in
        estimate.label.output_tokens = unlabelled * _LABEL_OUTPUT_TOKENS

    # Projected NEW clusters from this run's extractions (heuristic).
    projected_pps = int(estimate.extract.calls * _PROJECTED_PAINPOINT_RATIO)
    projected_clusters = projected_pps // _PROJECTED_CLUSTER_SIZE
    if projected_clusters:
        per_call_in = chars_to_tokens(
            label_prompt_chars(evidence_count=10, avg_evidence_chars=80, category_count=10)
        )
        estimate.label_projected.calls = projected_clusters
        estimate.label_projected.input_tokens = projected_clusters * per_call_in
        estimate.label_projected.output_tokens = projected_clusters * _LABEL_OUTPUT_TOKENS
        estimate.notes.append(
            f"Projected new clusters use heuristic: "
            f"{int(_PROJECTED_PAINPOINT_RATIO*100)}% extraction yield, "
            f"avg cluster size {_PROJECTED_CLUSTER_SIZE}."
        )

    # Embedding only billed by NIM. For Ollama/mock it's free.
    if settings.embedding_provider == "nim":
        # Embedding text is `f"{problem_text}. Audience: {audience}. {urgency_cue}"`
        # ≈ 200 chars per pain point. Use projected pain points.
        embed_chars = projected_pps * 200
        estimate.embed.calls = 1 if projected_pps else 0
        estimate.embed.input_tokens = chars_to_tokens(embed_chars)
        estimate.notes.append(
            "Embedding cost projected from extraction count × heuristic ratio."
        )
    else:
        estimate.notes.append(
            f"Embedding provider '{settings.embedding_provider}' is not token-billed."
        )

    estimate.notes.append(
        "Token counts use chars/4 heuristic (±20% vs. real Llama/Qwen tokenizer)."
    )
    return estimate
```

- [ ] **Step 4: Ask the operator to run the test and confirm PASS.**

`uv run pytest tests/test_token_estimator.py -v`. Expected: 6 passed. **Wait for paste-back.**

- [ ] **Step 5: Commit.**

```bash
git add app/pipeline/token_estimator.py tests/test_token_estimator.py
git commit -m "feat(token-estimator): add estimate_tokens() DB query function"
```

---

## Task 3: Wire `dry_run` through `bulk_backfill`

**Files:**
- Modify: `app/ingestion/backfill.py` (lines 38–143)

- [ ] **Step 1: Modify `BackfillReport` to carry an optional estimate.**

In `app/ingestion/backfill.py`, change the `BackfillReport` dataclass (line 38):

```python
from typing import Optional
from app.pipeline.token_estimator import TokenEstimate  # add this import near the top

@dataclass
class BackfillReport:
    history_days: int
    items_per_source: dict[str, int] = field(default_factory=dict)
    painpoints_created: int = 0
    candidates_created: int = 0
    labelled: int = 0
    duration_s: float = 0.0
    estimate: Optional[TokenEstimate] = None  # NEW

    def to_dict(self) -> dict:
        d = {
            "history_days": self.history_days,
            "items_per_source": self.items_per_source,
            "painpoints_created": self.painpoints_created,
            "candidates_created": self.candidates_created,
            "labelled": self.labelled,
            "duration_s": round(self.duration_s, 1),
        }
        if self.estimate is not None:
            d["estimate"] = self.estimate.to_dict()
        return d
```

- [ ] **Step 2: Add `dry_run` param to `bulk_backfill` and branch.**

Change the signature (line 58) and the post-ingestion section (lines 117–139):

```python
async def bulk_backfill(
    connectors: list[BaseConnector],
    llm: LLMAdapter,
    embedder: EmbeddingAdapter,
    settings: Settings,
    history_days: int = 30,
    extraction_limit: int | None = None,
    dry_run: bool = False,        # NEW
) -> BackfillReport:
    ...
    # 2. Run v4 pipeline OR estimate tokens
    from app.db import _get_session_factory
    session_factory = _get_session_factory()

    if dry_run:
        from app.pipeline.token_estimator import estimate_tokens
        report.estimate = await estimate_tokens(session_factory, settings)
        report.duration_s = time.monotonic() - start
        log.info(
            "bulk_backfill_dry_run_complete",
            component="backfill",
            **report.to_dict(),
        )
        return report

    try:
        from app.pipeline.orchestrator import run_pipeline
        pipeline_report = await run_pipeline(
            session_factory, llm, embedder, settings,
            since=since, extraction_limit=extraction_limit,
        )
        # ... existing assignments to report.painpoints_created etc. unchanged
    except BaseException as exc:
        # ... unchanged
        raise

    report.duration_s = time.monotonic() - start
    log.info("bulk_backfill_complete", component="backfill", **report.to_dict())
    return report
```

> Keep the existing `try/except` block intact for the non-dry-run path. Only the early-return-when-`dry_run` branch is new.

- [ ] **Step 3: Ask the operator to run the existing backfill tests to confirm no regressions.**

`uv run pytest tests/test_run_backfill_cli.py tests/test_run_backfill_progress.py -v`. Expected: all existing tests still pass. **Wait for paste-back.**

- [ ] **Step 4: Commit.**

```bash
git add app/ingestion/backfill.py
git commit -m "feat(backfill): add dry_run flag that estimates tokens instead of running pipeline"
```

---

## Task 4: `--dry-run` CLI flag + report printer

**Files:**
- Modify: `scripts/run_backfill.py` (lines 128–316)

- [ ] **Step 1: Add the `--dry-run` argument.**

In `_parse_args` (line 128), add:

```python
parser.add_argument(
    "--dry-run", action="store_true",
    help="Skip the pipeline; print an estimate of how many LLM tokens it would spend.",
)
```

- [ ] **Step 2: Pass `dry_run` to `bulk_backfill`.**

In `_run` (around line 276), change the call:

```python
report = await bulk_backfill(
    connectors, llm, embedder, settings,
    history_days=args.history_days,
    extraction_limit=args.max_extraction_items,
    dry_run=args.dry_run,        # NEW
)
```

- [ ] **Step 3: Add a Rich-formatted report printer.**

Add this helper in `scripts/run_backfill.py` (above `_run`):

```python
def _print_estimate(report: "BackfillReport", console) -> None:
    from rich.table import Table

    est = report.estimate
    if est is None:
        return
    table = Table(title="Dry-run token estimate", show_header=True, header_style="bold")
    table.add_column("Stage")
    table.add_column("Calls", justify="right")
    table.add_column("Input tokens", justify="right")
    table.add_column("Output tokens", justify="right")
    table.add_column("Subtotal", justify="right")

    def row(name: str, s) -> None:
        table.add_row(
            name,
            f"{s.calls:,}",
            f"{s.input_tokens:,}",
            f"{s.output_tokens:,}",
            f"{s.total:,}",
        )

    row("Extract (exact)", est.extract)
    row("Label (existing unlabelled)", est.label)
    row("Label (projected new)", est.label_projected)
    row("Embed", est.embed)
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]", "", "", "",
        f"[bold]{est.total_tokens:,}[/bold]",
    )
    console.print(table)
    for note in est.notes:
        console.print(f"[dim]• {note}[/dim]")
```

Then in `_run`, after `bulk_backfill` returns, branch the summary section (line 293):

```python
if args.dry_run:
    _section("6/6  Token estimate")
    _print_estimate(report, progress.console)
    print(json.dumps({"backfill_dry_run_report": report.to_dict()}))
    return report
```

Place this **before** the existing `_section("6/6  Summary")` block so the normal summary is skipped on dry-run.

- [ ] **Step 4: Ask the operator to manually run the dry-run end-to-end on the local DB.**

```bash
uv run python -m scripts.run_backfill --history-days 1 --llm-provider mock --embedding-provider mock --dry-run
```

Expected:
- Connector phase runs and shows progress.
- Pipeline phase is skipped.
- A "Dry-run token estimate" table is printed.
- A JSON line `{"backfill_dry_run_report": ...}` is printed at the end.
- No new pain points or candidates created in the DB.

**Wait for paste-back of the table + JSON before continuing.**

- [ ] **Step 5: Commit.**

```bash
git add scripts/run_backfill.py
git commit -m "feat(run-backfill): add --dry-run flag with Rich-formatted estimate report"
```

---

## Task 5: CLI integration test for `--dry-run`

**Files:**
- Modify: `tests/test_run_backfill_cli.py`

- [ ] **Step 1: Append the failing test.**

```python
def test_run_backfill_cli_dry_run_skips_pipeline(
    tmp_path,
    monkeypatch,
) -> None:
    """--dry-run ingests but does NOT create pain points; report carries an estimate."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dryrun.db'}"

    async def _fake_run(self, since=None, until=None) -> RunStatus:
        from app.db import get_session
        async with get_session() as session:
            session.add(SourceItem(
                source_type=self.source_type,
                external_id=f"{self.source_type}-dry-001",
                title="I wish there was a habit tracker",
                role="extraction",
            ))
            await session.commit()
        return RunStatus(source_type=self.source_type, last_status="ok", items_ingested=1)

    monkeypatch.setattr(GithubConnector, "run", _fake_run)
    monkeypatch.setattr(HNConnector, "run", _fake_run)
    monkeypatch.setattr(RedditConnector, "run", _fake_run)

    from scripts.run_backfill import main

    report = main([
        "--history-days", "1",
        "--llm-provider", "mock",
        "--embedding-provider", "mock",
        "--db-url", db_url,
        "--dry-run",
    ])

    assert isinstance(report, BackfillReport)
    assert report.painpoints_created == 0       # pipeline did NOT run
    assert report.estimate is not None
    assert report.estimate.extract.calls >= 3   # one per fake-ingested item
    assert report.estimate.total_tokens > 0
```

- [ ] **Step 2: Ask the operator to run it and confirm PASS.**

`uv run pytest tests/test_run_backfill_cli.py -v`. Expected: both tests pass.

- [ ] **Step 3: Commit.**

```bash
git add tests/test_run_backfill_cli.py
git commit -m "test(run-backfill): cover --dry-run CLI path"
```

---

## Verification (end-to-end)

1. **Unit tests pass:**
   `uv run pytest tests/test_token_estimator.py tests/test_run_backfill_cli.py tests/test_run_backfill_progress.py -v`

2. **Type check (if project uses mypy/pyright):**
   `uv run mypy app/pipeline/token_estimator.py app/ingestion/backfill.py scripts/run_backfill.py` *(optional — skip if no mypy config exists in the repo)*

3. **Manual smoke (local SQLite, mock providers):**
   ```bash
   uv run python -m scripts.run_backfill --history-days 7 --llm-provider mock --embedding-provider mock --dry-run
   ```
   Expect: ingestion runs, no pipeline runs, table prints, JSON line at end, no DB rows in `pain_points` or `opportunity_candidates` for this run.

4. **Sanity-check the number against a real small run:**
   ```bash
   # First: dry-run prediction
   uv run python -m scripts.run_backfill --history-days 1 --llm-provider nim --dry-run
   # Then: real run capped to 5 items
   uv run python -m scripts.run_backfill --history-days 1 --llm-provider nim --max-extraction-items 5
   ```
   The dry-run total should be within ~30% of `5 * (per-item-tokens-from-NIM-response)` extrapolated. If wildly off, recheck the heuristic constants in `token_estimator.py`.

---

## Out of scope (explicitly NOT in this plan)

- Real tokenizer (`tiktoken` / `transformers`). Char-based heuristic is sufficient for "should I run this or not" decisions. Upgrade later if precision matters.
- Recording actual token usage from real runs to recalibrate the heuristic constants — separate plan.
- Per-provider pricing math (e.g., USD cost). The estimator returns tokens; pricing is provider-specific and changes often.
- A `--dry-run` that *also* skips ingestion. If the operator wants to estimate without HTTP fetches, they can re-run after the first ingestion has populated the DB.
