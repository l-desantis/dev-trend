# Milestone 4 — Agent Graph — Implementation Plan

> **Date:** 2026-04-27
> **Milestone:** M4 (Agent Graph)
> **Executor note:** This plan is intended to be executed outside the planning session. Each task is TDD-structured (failing test → implementation → passing test → commit). Follow steps in order.

---

## Context

M3 closed the scoring loop: the daily `_scoring_job` aggregates signals into `NicheSignal` and writes a row to `NicheScoreHistory` per niche per day. Today, no code reads those scores back into a brief — the `app/agents/` and `app/tools/` directories are scaffolded but every file is empty (`state.py`, `graph.py`, `nodes.py`, `prompts.py`, plus `app/llm/ollama_adapter.py`).

M4 lights the agent up. It builds the linear LangGraph specified in project doc §11 — `fetcher → retriever → forecaster → reporter → reviewer` — wires the Ollama adapter (qwen2.5) into the reporter, validates briefs heuristically in the reviewer, and persists each run as an `OpportunityBrief` row. A new daily job in the existing `AsyncIOScheduler` runs the graph for every niche after scoring completes, so by 03:00 UTC the morning digest (M5-06) has fresh briefs to push.

**Design decisions (locked in this plan):**

1. **Forecaster reads, doesn't recompute.** `forecaster_node` reads the latest `NicheScoreHistory` row for the niche. If today's row is missing (cold start, manual trigger), it calls `score_niche(niche_id, as_of)` to compute one. This matches the daily timing (scoring at 02:15 UTC, briefs at 03:00 UTC) and keeps the graph idempotent.
2. **Headline is programmatic, summary is LLM.** The reporter constructs `headline = f"{niche.name} — Score {round(score)}"` deterministically, and uses qwen2.5 only for the prose summary. This avoids JSON-schema parsing fragility (Risk Register §16 "qwen2.5 response schema non-compliance").
3. **Reviewer is heuristic, not LLM.** `reviewer_node` checks summary length, placeholder markers, and evidence count. No second LLM call. Sets `has_issues=True` and logs gaps; never retries.
4. **Brief persistence happens in the orchestrator, not in a node.** A `run_brief_for_niche(niche_id, adapter, as_of)` function in `app/agents/graph.py` invokes the compiled graph and writes `OpportunityBrief` from the final state. Idempotency: delete-then-insert per `(niche_id, date(generated_at))` UTC, mirroring M3's scoring approach.
5. **LLM adapter injected at build time.** `build_graph(adapter: LLMAdapter)` returns a compiled graph bound to that adapter. Tests pass `MockLLMAdapter()`; the scheduler instantiates `OllamaAdapter` from settings. No global singleton.
6. **Brief job runs at 03:00 UTC** (configurable), after the 02:15 scoring job, before the 08:00 digest.

**Already done:** `app/llm/base.py:5-13` defines the `LLMAdapter` ABC, `app/llm/mock_adapter.py:14-22` provides `MockLLMAdapter` (deterministic fixture), `app/models.py:88-106` defines `OpportunityBrief` with all 11 fields. `tests/test_agent_graph.py:1-27` already smoke-tests the mock adapter; this plan extends that file.

---

## File Structure

**New files:**
- `app/agents/state.py` — `OpportunityState` TypedDict.
- `app/agents/prompts.py` — Brief generation prompt template + helpers.
- `app/agents/nodes.py` — `fetcher_node`, `retriever_node`, `forecaster_node`, `reporter_node`, `reviewer_node`.
- `app/agents/graph.py` — `build_graph(adapter)` + `run_brief_for_niche(niche_id, adapter, as_of)` orchestrator + brief persistence.
- `app/llm/ollama_adapter.py` — `OllamaAdapter` calling `ollama.AsyncClient`.
- `scripts/run_agent.py` — manual smoke runner.
- `tests/test_ollama_adapter.py`
- `tests/test_agent_nodes.py`
- `tests/test_agent_graph_e2e.py`

**Modified files:**
- `app/config.py` — add brief job cron + reporter timeout + LLM provider settings (after the Scoring block at line 89).
- `app/ingestion/scheduler.py` — add `daily_brief_generation` job mirroring `daily_scoring` (after line 61).
- `app/agents/__init__.py` — re-export `run_brief_for_niche`, `build_graph`.
- `tests/test_agent_graph.py` — extend the existing 27-line file with adapter-pluggable graph tests.
- `KANBAN.md` — flip M4-01 … M4-10 to Done.
- `docs/decisions.md` — append ADR-005 (agent graph design).

**Untouched (but referenced):**
- `app/llm/base.py:5-13` — `LLMAdapter` ABC. No change.
- `app/llm/mock_adapter.py:14-22` — used by all graph tests.
- `app/models.py:88-106` — `OpportunityBrief` schema. No change.
- `app/forecasting/scoring.py:116, 175` — `score_niche()` and `score_all_niches()`; the forecaster node calls `score_niche()` only as a cold-start fallback.
- `app/db.py:27-30` — reuse `get_session()`.
- `app/ingestion/scheduler.py:40-61` — the existing `_scoring_job` is the template for the new `_brief_job`.

---

## Implementation Idioms (follow existing patterns)

- **Async DB access:** `async with get_session() as session: ...; await session.commit()` (db.py:27-30).
- **Tests:** plain `async def test_*` (pytest-asyncio is in auto mode per `pyproject.toml:43-44`). Call `await init_db()` inside each test that touches the DB.
- **Structured logging:** `log = structlog.get_logger(__name__)`; emit `log.info("event", component="…", …)` matching the M3 style at `app/forecasting/scoring.py`.
- **Settings access:** `from app.config import get_settings; settings = get_settings()`.
- **No global LLM adapter.** Inject via function parameter so tests can pass `MockLLMAdapter()`.

---

## Task 1 — Config settings for brief job & LLM provider

**Files:**
- Modify: `app/config.py` (insert a new "Agent / brief generation" block after the Scoring block at line 89)

- [ ] **Step 1: Add settings**

In `app/config.py`, after the existing `scoring_cron_minute: int = 15` line (line 89), add:

```python
    # Agent / brief generation
    llm_provider: str = "ollama"          # "ollama" | "mock"
    brief_cron_hour: int = 3
    brief_cron_minute: int = 0
    brief_per_niche_timeout_s: float = 90.0
    brief_max_evidence_items: int = 5
    brief_min_summary_chars: int = 50
```

- [ ] **Step 2: Sanity check**

Run: `python -c "from app.config import get_settings; s = get_settings(); print(s.llm_provider, s.brief_per_niche_timeout_s)"`
Expected: `ollama 90.0`

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat(agents): add brief-generation config settings"
```

---

## Task 2 — `OpportunityState` TypedDict (TDD)

The state shape per project doc §11 — niche, source_items, signals, forecast, scorecard, brief, errors, triggered_by. All optional via `total=False` so nodes can populate progressively.

**Files:**
- Create: `app/agents/state.py`
- Test: extend `tests/test_agent_graph.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_agent_graph.py` (after the existing 27 lines):

```python
from app.agents.state import OpportunityState


def test_opportunity_state_accepts_all_documented_keys():
    state: OpportunityState = {
        "niche": {"id": 1, "slug": "x", "name": "X"},
        "source_items": [],
        "signals": [],
        "forecast": {"label": "Stable", "slope": 0.0},
        "scorecard": {"score_total": 50.0, "breakdown": {}},
        "brief": {"headline": "h", "summary": "s", "evidence": [],
                  "forecast_label": "Stable", "has_issues": False,
                  "model_name": "qwen2.5"},
        "errors": [],
        "triggered_by": "scheduler",
    }
    assert state["triggered_by"] == "scheduler"
    assert state["niche"]["slug"] == "x"


def test_opportunity_state_allows_partial_population():
    state: OpportunityState = {"niche": {"id": 1}, "errors": []}
    assert "source_items" not in state
```

- [ ] **Step 2: Run — confirm failure**

Run: `pytest tests/test_agent_graph.py -v`
Expected: `ModuleNotFoundError: No module named 'app.agents.state'` (the new tests fail).

- [ ] **Step 3: Implement**

Create `app/agents/state.py`:

```python
"""Shared LangGraph state for the opportunity-brief agent.

Matches project doc §11 exactly. `total=False` lets nodes populate
progressively without exhaustive initialisation.
"""
from typing import TypedDict


class OpportunityState(TypedDict, total=False):
    niche: dict
    source_items: list
    signals: list
    forecast: dict
    scorecard: dict
    brief: dict
    errors: list
    triggered_by: str
```

- [ ] **Step 4: Run — confirm pass**

Run: `pytest tests/test_agent_graph.py -v`
Expected: all tests pass (the existing 3 + the 2 new ones).

- [ ] **Step 5: Commit**

```bash
git add app/agents/state.py tests/test_agent_graph.py
git commit -m "feat(agents): add OpportunityState TypedDict"
```

---

## Task 3 — Prompt templates

The reporter calls the LLM once per niche with a structured prompt rendered from the agent state. Keep templates in a single module so prompt iteration doesn't touch node logic.

**Files:**
- Create: `app/agents/prompts.py`
- Test: extend `tests/test_agent_graph.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_agent_graph.py`:

```python
from app.agents.prompts import render_brief_prompt


def test_render_brief_prompt_includes_niche_and_score():
    context = {
        "niche": {"name": "AI Habit Trackers", "slug": "ai-habit",
                  "category": "wellness", "summary": "Habit-tracking apps"},
        "scorecard": {"score_total": 84.2, "breakdown": {
            "growth": {"raw": 0.5, "normalized": 78.0},
            "demand": {"raw": 12.0, "normalized": 65.0},
            "novelty": {"raw": 0.9, "normalized": 90.0},
        }},
        "forecast": {"label": "Rising", "slope": 0.5},
        "evidence": [
            {"source_type": "github", "title": "habit-tracker repo",
             "url": "https://example.com", "excerpt": "Stars rising"},
        ],
    }
    prompt = render_brief_prompt(context)
    assert "AI Habit Trackers" in prompt
    assert "84" in prompt
    assert "Rising" in prompt
    assert "habit-tracker repo" in prompt
    assert "github" in prompt


def test_render_brief_prompt_handles_no_evidence():
    context = {
        "niche": {"name": "X", "slug": "x", "category": "c", "summary": ""},
        "scorecard": {"score_total": 0.0, "breakdown": {
            "growth": {"raw": 0, "normalized": 0},
            "demand": {"raw": 0, "normalized": 0},
            "novelty": {"raw": 0, "normalized": 0},
        }},
        "forecast": {"label": "Stable", "slope": 0.0},
        "evidence": [],
    }
    prompt = render_brief_prompt(context)
    assert "X" in prompt
    assert "no evidence" in prompt.lower() or "none" in prompt.lower()
```

- [ ] **Step 2: Run — confirm failure**

Run: `pytest tests/test_agent_graph.py -v`
Expected: `ModuleNotFoundError: No module named 'app.agents.prompts'`.

- [ ] **Step 3: Implement**

Create `app/agents/prompts.py`:

```python
"""Prompt templates for the opportunity-brief agent."""
from typing import Any

BRIEF_SYSTEM_PROMPT = (
    "You are DevTrend, an analyst writing concise market-opportunity briefs "
    "for indie developers. Briefs must be grounded in the evidence supplied; "
    "do not invent sources, numbers, or trends."
)

_BRIEF_TEMPLATE = """Write a 3-5 sentence opportunity brief for the niche below.

Niche: {name} ({category})
Slug: {slug}
Summary: {summary}

Composite score: {score:.0f}/100
- Growth (weight 0.41): raw={growth_raw}, normalized={growth_norm:.0f}
- Demand (weight 0.35): raw={demand_raw}, normalized={demand_norm:.0f}
- Novelty (weight 0.24): raw={novelty_raw}, normalized={novelty_norm:.0f}

Trend direction: {forecast_label} (7-day slope = {slope})

Evidence (top {evidence_count}):
{evidence_block}

Rules:
- 3-5 sentences total. No bullet lists, no markdown headings.
- Reference at least one specific evidence item by source type.
- State the trend direction explicitly.
- Do not invent metrics not shown above.
"""


def _format_evidence(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(no evidence available)"
    lines = []
    for i, item in enumerate(items, 1):
        src = item.get("source_type", "?")
        title = item.get("title", "(untitled)")
        excerpt = item.get("excerpt") or item.get("body") or ""
        excerpt = excerpt[:200].replace("\n", " ").strip()
        lines.append(f"{i}. [{src}] {title} — {excerpt}")
    return "\n".join(lines)


def render_brief_prompt(context: dict[str, Any]) -> str:
    """Render the user-prompt body for `LLMAdapter.generate_brief()`.

    `context` must contain `niche`, `scorecard`, `forecast`, `evidence`.
    """
    niche = context["niche"]
    scorecard = context["scorecard"]
    forecast = context["forecast"]
    evidence = context.get("evidence", [])
    breakdown = scorecard.get("breakdown", {})
    growth = breakdown.get("growth", {"raw": 0, "normalized": 0})
    demand = breakdown.get("demand", {"raw": 0, "normalized": 0})
    novelty = breakdown.get("novelty", {"raw": 0, "normalized": 0})
    return _BRIEF_TEMPLATE.format(
        name=niche.get("name", "?"),
        category=niche.get("category", "?"),
        slug=niche.get("slug", "?"),
        summary=niche.get("summary", "") or "(none)",
        score=scorecard.get("score_total", 0.0),
        growth_raw=round(float(growth.get("raw", 0)), 3),
        growth_norm=float(growth.get("normalized", 0)),
        demand_raw=round(float(demand.get("raw", 0)), 3),
        demand_norm=float(demand.get("normalized", 0)),
        novelty_raw=round(float(novelty.get("raw", 0)), 3),
        novelty_norm=float(novelty.get("normalized", 0)),
        forecast_label=forecast.get("label", "Stable"),
        slope=round(float(forecast.get("slope", 0.0)), 3),
        evidence_count=len(evidence),
        evidence_block=_format_evidence(evidence),
    )
```

- [ ] **Step 4: Run — confirm pass**

Run: `pytest tests/test_agent_graph.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/agents/prompts.py tests/test_agent_graph.py
git commit -m "feat(agents): add brief generation prompt template"
```

---

## Task 4 — Ollama adapter (TDD with mocked `ollama.AsyncClient`)

The `ollama` package (pinned in `pyproject.toml:18`) provides `ollama.AsyncClient`. Tests should NOT hit a real Ollama server — patch the client.

**Files:**
- Create: `app/llm/ollama_adapter.py`
- Test: `tests/test_ollama_adapter.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_ollama_adapter.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.llm.ollama_adapter import OllamaAdapter


def _fake_chat_response(text: str):
    return SimpleNamespace(message=SimpleNamespace(content=text))


async def test_generate_brief_returns_model_text():
    adapter = OllamaAdapter(base_url="http://x", model="qwen2.5")
    with patch.object(
        adapter._client, "chat", new=AsyncMock(return_value=_fake_chat_response("hello world"))
    ) as chat:
        out = await adapter.generate_brief({
            "niche": {"name": "X", "slug": "x", "category": "c", "summary": ""},
            "scorecard": {"score_total": 50.0, "breakdown": {
                "growth": {"raw": 0, "normalized": 50},
                "demand": {"raw": 0, "normalized": 50},
                "novelty": {"raw": 0, "normalized": 50},
            }},
            "forecast": {"label": "Stable", "slope": 0.0},
            "evidence": [],
        })
    assert out == "hello world"
    chat.assert_awaited_once()
    kwargs = chat.await_args.kwargs
    assert kwargs["model"] == "qwen2.5"
    messages = kwargs["messages"]
    assert any(m["role"] == "system" for m in messages)
    assert any("X" in m["content"] for m in messages)


async def test_summarize_evidence_returns_string():
    adapter = OllamaAdapter(base_url="http://x", model="qwen2.5")
    with patch.object(
        adapter._client, "chat", new=AsyncMock(return_value=_fake_chat_response("summary"))
    ):
        out = await adapter.summarize_evidence([{"title": "t", "source_type": "github"}])
    assert out == "summary"


async def test_review_brief_returns_no_issues_dict():
    """The LLM-side review is heuristic-only in Phase 1; the adapter just
    delegates to a deterministic check so callers can rely on the shape."""
    adapter = OllamaAdapter(base_url="http://x", model="qwen2.5")
    out = await adapter.review_brief("a sufficiently long brief " * 5)
    assert isinstance(out, dict)
    assert out["has_issues"] is False
    assert out["gaps"] == []


async def test_review_brief_flags_short_text():
    adapter = OllamaAdapter(base_url="http://x", model="qwen2.5")
    out = await adapter.review_brief("short")
    assert out["has_issues"] is True
    assert "summary" in " ".join(out["gaps"]).lower() or out["gaps"]
```

- [ ] **Step 2: Run — confirm failure**

Run: `pytest tests/test_ollama_adapter.py -v`
Expected: `ModuleNotFoundError: No module named 'app.llm.ollama_adapter'` (the file is currently empty — `from app.llm.ollama_adapter import OllamaAdapter` fails).

- [ ] **Step 3: Implement**

Replace the contents of `app/llm/ollama_adapter.py` (currently empty) with:

```python
"""Ollama adapter for the local qwen2.5 model.

Calls only `ollama.AsyncClient.chat`. The reviewer step is intentionally
heuristic in Phase 1 — see ADR-005 — so `review_brief()` does not call the
LLM; it returns a deterministic shape that the agent's reviewer_node can
trust.
"""
from typing import Any

import ollama

from app.agents.prompts import BRIEF_SYSTEM_PROMPT, render_brief_prompt
from app.llm.base import LLMAdapter

_MIN_REVIEWABLE_CHARS = 50


class OllamaAdapter(LLMAdapter):
    def __init__(self, base_url: str, model: str) -> None:
        self._client = ollama.AsyncClient(host=base_url)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def generate_brief(self, context: dict[str, Any]) -> str:
        prompt = render_brief_prompt(context)
        response = await self._client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.message.content

    async def summarize_evidence(self, items: list[Any]) -> str:
        bullet = "\n".join(
            f"- [{i.get('source_type', '?')}] {i.get('title', '')}"
            for i in items
        )
        response = await self._client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": "Summarise the evidence in one sentence."},
                {"role": "user", "content": bullet or "(no items)"},
            ],
        )
        return response.message.content

    async def review_brief(self, brief: str) -> dict[str, object]:
        gaps: list[str] = []
        if len(brief.strip()) < _MIN_REVIEWABLE_CHARS:
            gaps.append("summary too short")
        return {"has_issues": bool(gaps), "gaps": gaps}
```

- [ ] **Step 4: Run — confirm pass**

Run: `pytest tests/test_ollama_adapter.py -v`
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/llm/ollama_adapter.py tests/test_ollama_adapter.py
git commit -m "feat(llm): add Ollama adapter (qwen2.5)"
```

---

## Task 5 — `fetcher_node` (TDD)

Loads the niche row + the most recent `SourceItem` rows for that niche from the DB and writes them to `state["niche"]` and `state["source_items"]`.

**Files:**
- Create: `app/agents/nodes.py` (initial stub with this node)
- Test: `tests/test_agent_nodes.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_agent_nodes.py`:

```python
from datetime import UTC, datetime, timedelta

from app.agents.nodes import fetcher_node
from app.db import get_session, init_db
from app.models import Niche, SourceItem


async def _mk_niche(slug: str = "alpha", name: str = "Alpha", category: str = "devtools") -> int:
    async with get_session() as session:
        n = Niche(slug=slug, name=name, category=category, summary="s",
                  keywords_json=[slug])
        session.add(n)
        await session.commit()
        await session.refresh(n)
        return n.id


async def _mk_item(niche_id: int, source_type: str, external_id: str,
                   ingested_at: datetime, **md) -> None:
    async with get_session() as session:
        session.add(SourceItem(
            source_type=source_type, external_id=external_id,
            title=f"title-{external_id}", body="body", url="u",
            created_at=ingested_at, ingested_at=ingested_at,
            niche_id=niche_id, metadata_json=md,
        ))
        await session.commit()


async def test_fetcher_loads_niche_and_recent_items():
    await init_db()
    nid = await _mk_niche()
    now = datetime.now(UTC)  # use real now so fetcher's _utcnow()-based cutoff matches
    await _mk_item(nid, "github", "g1", now, stars=10)
    await _mk_item(nid, "hn", "h1", now - timedelta(days=1), points=20)

    state = await fetcher_node({"niche": {"id": nid}, "errors": []})

    assert state["niche"]["slug"] == "alpha"
    assert state["niche"]["category"] == "devtools"
    assert len(state["source_items"]) == 2
    # Newest first
    assert state["source_items"][0]["external_id"] == "g1"


async def test_fetcher_records_error_when_niche_missing():
    await init_db()

    state = await fetcher_node({"niche": {"id": 9999}, "errors": []})

    assert state["niche"].get("slug") in (None, "")
    assert state["source_items"] == []
    assert state["errors"]
    assert state["errors"][0]["component"] == "fetcher_node"


async def test_fetcher_caps_items_at_window():
    await init_db()
    nid = await _mk_niche("beta", "Beta")
    now = datetime.now(UTC)
    # Spread 40 items across days 0..39; the 30-day cutoff should drop the oldest 10.
    for i in range(40):
        await _mk_item(nid, "github", f"g{i}", now - timedelta(days=i))

    state = await fetcher_node({"niche": {"id": nid}, "errors": []})

    # 30-day window keeps days 0..29 → at most 30 items
    assert len(state["source_items"]) <= 30
    assert len(state["source_items"]) >= 28  # tolerate clock drift around midnight
```

- [ ] **Step 2: Run — confirm failure**

Run: `pytest tests/test_agent_nodes.py -v`
Expected: `ImportError: cannot import name 'fetcher_node'` (file is empty).

- [ ] **Step 3: Implement**

Replace the contents of `app/agents/nodes.py` (currently empty) with:

```python
"""LangGraph node functions for the opportunity-brief agent.

Each node accepts and returns the full `OpportunityState` dict so LangGraph
can merge keys back into the channel automatically.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from app.agents.state import OpportunityState
from app.config import get_settings
from app.db import get_session
from app.forecasting.scoring import score_niche
from app.llm.base import LLMAdapter
from app.models import Niche, NicheScoreHistory, NicheSignal, SourceItem

log = structlog.get_logger(__name__)

_SOURCE_ITEM_LOOKBACK_DAYS = 30
_SOURCE_ITEM_LIMIT = 50


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _record_error(state: OpportunityState, component: str, message: str) -> None:
    state.setdefault("errors", []).append({
        "component": component,
        "message": message,
        "at": _utcnow().isoformat(),
    })


def _serialise_item(item: SourceItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "source_type": item.source_type,
        "external_id": item.external_id,
        "title": item.title,
        "body": item.body,
        "url": item.url,
        "created_at": (item.created_at or item.ingested_at).isoformat(),
        "ingested_at": item.ingested_at.isoformat(),
        "metadata": item.metadata_json or {},
    }


async def fetcher_node(state: OpportunityState) -> OpportunityState:
    niche_id = state.get("niche", {}).get("id")
    state.setdefault("errors", [])

    if not niche_id:
        _record_error(state, "fetcher_node", "missing niche.id")
        state["source_items"] = []
        return state

    async with get_session() as session:
        niche = (await session.execute(
            select(Niche).where(Niche.id == niche_id)
        )).scalar_one_or_none()

        if niche is None:
            _record_error(state, "fetcher_node", f"niche {niche_id} not found")
            state["niche"] = {"id": niche_id}
            state["source_items"] = []
            return state

        cutoff = _utcnow() - timedelta(days=_SOURCE_ITEM_LOOKBACK_DAYS)
        items = (await session.execute(
            select(SourceItem)
            .where(
                SourceItem.niche_id == niche_id,
                SourceItem.ingested_at >= cutoff,
            )
            .order_by(SourceItem.ingested_at.desc())
            .limit(_SOURCE_ITEM_LIMIT)
        )).scalars().all()

    state["niche"] = {
        "id": niche.id,
        "slug": niche.slug,
        "name": niche.name,
        "category": niche.category,
        "summary": niche.summary,
    }
    state["source_items"] = [_serialise_item(i) for i in items]
    log.info("Fetcher complete", component="fetcher_node",
             niche_id=niche_id, items=len(items))
    return state
```

- [ ] **Step 4: Run — confirm pass**

Run: `pytest tests/test_agent_nodes.py -v`
Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/agents/nodes.py tests/test_agent_nodes.py
git commit -m "feat(agents): add fetcher_node"
```

---

## Task 6 — `retriever_node` (TDD)

Loads `NicheSignal` aggregates for the niche over the last 7 days into `state["signals"]`. The reporter and forecaster both use this.

**Files:**
- Modify: `app/agents/nodes.py`
- Test: extend `tests/test_agent_nodes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_agent_nodes.py`:

```python
from app.agents.nodes import retriever_node
from app.features.signal_aggregator import aggregate_daily_signals


async def test_retriever_loads_signals_for_niche():
    await init_db()
    nid = await _mk_niche()
    now = datetime.now(UTC)  # retriever's 7-day cutoff is `_utcnow()` based
    await _mk_item(nid, "github", "g1", now, stars=100)
    await aggregate_daily_signals(now)

    state = await retriever_node({"niche": {"id": nid}, "errors": []})

    assert state["signals"]
    metrics = {s["metric_name"] for s in state["signals"]}
    assert "mention_count" in metrics
    assert "github_stars_total" in metrics


async def test_retriever_returns_empty_when_no_signals():
    await init_db()
    nid = await _mk_niche()

    state = await retriever_node({"niche": {"id": nid}, "errors": []})

    assert state["signals"] == []
    assert not state.get("errors")
```

- [ ] **Step 2: Run — confirm failure**

Run: `pytest tests/test_agent_nodes.py -v`
Expected: `ImportError: cannot import name 'retriever_node'`.

- [ ] **Step 3: Implement**

Append to `app/agents/nodes.py`:

```python
_SIGNAL_LOOKBACK_DAYS = 7


async def retriever_node(state: OpportunityState) -> OpportunityState:
    niche_id = state.get("niche", {}).get("id")
    state.setdefault("errors", [])
    state["signals"] = []

    if not niche_id:
        _record_error(state, "retriever_node", "missing niche.id")
        return state

    cutoff = _utcnow() - timedelta(days=_SIGNAL_LOOKBACK_DAYS)
    async with get_session() as session:
        rows = (await session.execute(
            select(NicheSignal)
            .where(
                NicheSignal.niche_id == niche_id,
                NicheSignal.metric_timestamp >= cutoff,
            )
            .order_by(NicheSignal.metric_timestamp.desc())
        )).scalars().all()

    state["signals"] = [
        {
            "source_type": r.source_type,
            "metric_name": r.metric_name,
            "metric_value": float(r.metric_value),
            "metric_timestamp": r.metric_timestamp.isoformat(),
        }
        for r in rows
    ]
    log.info("Retriever complete", component="retriever_node",
             niche_id=niche_id, signals=len(rows))
    return state
```

- [ ] **Step 4: Run — confirm pass**

Run: `pytest tests/test_agent_nodes.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/agents/nodes.py tests/test_agent_nodes.py
git commit -m "feat(agents): add retriever_node"
```

---

## Task 7 — `forecaster_node` (TDD)

Reads the latest `NicheScoreHistory` row for today; if missing, calls `score_niche()` to compute one. Populates `state["scorecard"]` and derives `state["forecast"]` (label = Rising/Stable/Declining based on `growth.raw` sign).

**Files:**
- Modify: `app/agents/nodes.py`
- Test: extend `tests/test_agent_nodes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_agent_nodes.py`:

```python
from app.agents.nodes import forecaster_node


async def test_forecaster_reads_existing_score():
    await init_db()
    nid = await _mk_niche()
    now = datetime.now(UTC)
    await _mk_item(nid, "github", "g1", now, stars=100)
    await aggregate_daily_signals(now)

    # Pre-compute a score
    from app.forecasting.scoring import score_niche
    await score_niche(nid, now)

    state = await forecaster_node({"niche": {"id": nid}, "errors": []}, as_of=now)

    assert "scorecard" in state
    assert state["scorecard"]["score_total"] >= 0.0
    assert "growth" in state["scorecard"]["breakdown"]
    assert state["forecast"]["label"] in ("Rising", "Stable", "Declining")


async def test_forecaster_computes_when_no_score_exists():
    await init_db()
    nid = await _mk_niche()
    now = datetime.now(UTC)
    await _mk_item(nid, "github", "g1", now, stars=100)
    await aggregate_daily_signals(now)
    # Note: do NOT call score_niche — forecaster should cold-start it

    state = await forecaster_node({"niche": {"id": nid}, "errors": []}, as_of=now)

    assert state["scorecard"]["score_total"] >= 0.0


async def test_forecaster_label_reflects_growth_sign():
    """A niche with strictly increasing daily mentions yields Rising."""
    await init_db()
    nid = await _mk_niche("rising", "Rising")
    base = datetime.now(UTC)
    # Create progressively more items per day for 7 days → positive slope
    for d in range(7):
        for i in range(d + 1):
            await _mk_item(
                nid, "github", f"g-{d}-{i}",
                base - timedelta(days=6 - d),
                stars=10,
            )
        await aggregate_daily_signals(base - timedelta(days=6 - d))

    state = await forecaster_node({"niche": {"id": nid}, "errors": []}, as_of=base)

    assert state["forecast"]["label"] == "Rising"
    assert state["forecast"]["slope"] > 0.0
```

- [ ] **Step 2: Run — confirm failure**

Run: `pytest tests/test_agent_nodes.py -v`
Expected: `ImportError: cannot import name 'forecaster_node'`.

- [ ] **Step 3: Implement**

Append to `app/agents/nodes.py`:

```python
def _forecast_label(growth_raw: float) -> str:
    if growth_raw > 0.05:
        return "Rising"
    if growth_raw < -0.05:
        return "Declining"
    return "Stable"


def _start_of_day(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def forecaster_node(
    state: OpportunityState, *, as_of: datetime | None = None
) -> OpportunityState:
    niche_id = state.get("niche", {}).get("id")
    state.setdefault("errors", [])
    when = as_of or _utcnow()
    today = _start_of_day(when)

    if not niche_id:
        _record_error(state, "forecaster_node", "missing niche.id")
        return state

    async with get_session() as session:
        row = (await session.execute(
            select(NicheScoreHistory)
            .where(
                NicheScoreHistory.niche_id == niche_id,
                NicheScoreHistory.scored_at == today,
            )
        )).scalar_one_or_none()

    if row is None:
        try:
            row = await score_niche(niche_id, when)
        except Exception as exc:
            _record_error(state, "forecaster_node", f"score_niche failed: {exc}")
            return state

    breakdown = row.score_breakdown_json or {}
    growth_raw = float(breakdown.get("growth", {}).get("raw", 0.0))

    state["scorecard"] = {
        "score_total": float(row.score_total),
        "breakdown": breakdown,
        "scored_at": row.scored_at.isoformat(),
    }
    state["forecast"] = {
        "label": _forecast_label(growth_raw),
        "slope": growth_raw,
    }
    log.info("Forecaster complete", component="forecaster_node",
             niche_id=niche_id, score_total=round(state["scorecard"]["score_total"], 2))
    return state
```

- [ ] **Step 4: Run — confirm pass**

Run: `pytest tests/test_agent_nodes.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/agents/nodes.py tests/test_agent_nodes.py
git commit -m "feat(agents): add forecaster_node"
```

---

## Task 8 — `reporter_node` (TDD)

Builds the LLM context, calls `adapter.generate_brief()` under `asyncio.wait_for(timeout=settings.brief_per_niche_timeout_s)`, sets `state["brief"]`. Headline is programmatic; summary is LLM output. Evidence list is a denormalised snapshot of the top-N source items.

**Files:**
- Modify: `app/agents/nodes.py`
- Test: extend `tests/test_agent_nodes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_agent_nodes.py`:

```python
from app.agents.nodes import reporter_node
from app.llm.mock_adapter import MockLLMAdapter


async def test_reporter_builds_brief_from_state():
    state: OpportunityState = {
        "niche": {"id": 1, "slug": "x", "name": "X", "category": "c", "summary": "s"},
        "source_items": [
            {"source_type": "github", "external_id": "g1", "title": "t1",
             "url": "u1", "body": "b1",
             "created_at": "2026-04-27T00:00:00+00:00",
             "ingested_at": "2026-04-27T00:00:00+00:00", "metadata": {}},
        ],
        "signals": [],
        "scorecard": {"score_total": 70.0, "breakdown": {
            "growth": {"raw": 0.5, "normalized": 80.0},
            "demand": {"raw": 10.0, "normalized": 60.0},
            "novelty": {"raw": 0.9, "normalized": 90.0},
        }},
        "forecast": {"label": "Rising", "slope": 0.5},
        "errors": [],
    }
    out = await reporter_node(state, adapter=MockLLMAdapter())

    assert "brief" in out
    brief = out["brief"]
    assert brief["headline"]
    assert "X" in brief["headline"]
    assert "70" in brief["headline"]
    assert brief["summary"]
    assert brief["forecast_label"] == "Rising"
    assert brief["model_name"] == "MockLLMAdapter"
    assert len(brief["evidence"]) == 1
    assert brief["evidence"][0]["source_type"] == "github"


async def test_reporter_handles_timeout():
    """A slow adapter should yield an empty-summary brief, not crash."""
    import asyncio as _asyncio

    class SlowAdapter(MockLLMAdapter):
        async def generate_brief(self, context):  # type: ignore[override]
            await _asyncio.sleep(5.0)
            return "should not arrive"

    state: OpportunityState = {
        "niche": {"id": 1, "slug": "x", "name": "X", "category": "c", "summary": ""},
        "source_items": [],
        "signals": [],
        "scorecard": {"score_total": 0.0, "breakdown": {
            "growth": {"raw": 0, "normalized": 0},
            "demand": {"raw": 0, "normalized": 0},
            "novelty": {"raw": 0, "normalized": 0},
        }},
        "forecast": {"label": "Stable", "slope": 0.0},
        "errors": [],
    }
    out = await reporter_node(state, adapter=SlowAdapter(), timeout=0.05)

    assert out["brief"]["summary"] == ""
    assert any(e["component"] == "reporter_node" for e in out["errors"])
```

- [ ] **Step 2: Run — confirm failure**

Run: `pytest tests/test_agent_nodes.py -v`
Expected: `ImportError: cannot import name 'reporter_node'`.

- [ ] **Step 3: Implement**

Append to `app/agents/nodes.py`:

```python
def _build_evidence(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    out = []
    for item in items[:limit]:
        body = item.get("body") or ""
        excerpt = body[:240].replace("\n", " ").strip()
        out.append({
            "source_type": item.get("source_type"),
            "external_id": item.get("external_id"),
            "title": item.get("title"),
            "url": item.get("url"),
            "created_at": item.get("created_at"),
            "excerpt": excerpt,
        })
    return out


async def reporter_node(
    state: OpportunityState,
    *,
    adapter: LLMAdapter,
    timeout: float | None = None,
) -> OpportunityState:
    state.setdefault("errors", [])
    settings = get_settings()
    timeout_s = timeout if timeout is not None else settings.brief_per_niche_timeout_s
    max_evidence = settings.brief_max_evidence_items

    niche = state.get("niche", {}) or {}
    scorecard = state.get("scorecard", {}) or {}
    forecast = state.get("forecast", {}) or {}
    source_items = state.get("source_items", []) or []
    evidence = _build_evidence(source_items, max_evidence)

    score_total = float(scorecard.get("score_total", 0.0))
    headline = f"{niche.get('name', 'Unknown')} — Score {round(score_total)}"
    forecast_label = forecast.get("label", "Stable")
    model_name = type(adapter).__name__

    context = {
        "niche": niche,
        "scorecard": scorecard,
        "forecast": forecast,
        "evidence": evidence,
    }

    summary = ""
    try:
        summary = await asyncio.wait_for(
            adapter.generate_brief(context),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        _record_error(state, "reporter_node",
                      f"generate_brief timed out after {timeout_s}s")
        log.error("Reporter timeout", component="reporter_node",
                  niche_id=niche.get("id"), timeout_s=timeout_s)
    except Exception as exc:
        _record_error(state, "reporter_node", f"generate_brief failed: {exc}")
        log.error("Reporter failed", component="reporter_node",
                  niche_id=niche.get("id"), error=str(exc))

    state["brief"] = {
        "headline": headline,
        "summary": summary or "",
        "evidence": evidence,
        "forecast_label": forecast_label,
        "has_issues": False,  # reviewer_node sets this
        "model_name": model_name,
    }
    log.info("Reporter complete", component="reporter_node",
             niche_id=niche.get("id"),
             summary_chars=len(state["brief"]["summary"]))
    return state
```

- [ ] **Step 4: Run — confirm pass**

Run: `pytest tests/test_agent_nodes.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/agents/nodes.py tests/test_agent_nodes.py
git commit -m "feat(agents): add reporter_node with per-niche timeout"
```

---

## Task 9 — `reviewer_node` (TDD heuristic)

Heuristic completeness check (no LLM call). Sets `has_issues=True` and logs gaps when:
- summary length < `settings.brief_min_summary_chars`
- summary contains placeholder markers (`TODO`, `[INSERT`, `<placeholder>`)
- evidence list is empty

**Files:**
- Modify: `app/agents/nodes.py`
- Test: extend `tests/test_agent_nodes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_agent_nodes.py`:

```python
from app.agents.nodes import reviewer_node


def _brief_state(summary: str, evidence: list | None = None) -> dict:
    return {
        "niche": {"id": 1, "slug": "x", "name": "X"},
        "brief": {
            "headline": "X — 70",
            "summary": summary,
            "evidence": evidence if evidence is not None else [{"source_type": "github"}],
            "forecast_label": "Rising",
            "has_issues": False,
            "model_name": "MockLLMAdapter",
        },
        "errors": [],
    }


async def test_reviewer_passes_complete_brief():
    state = _brief_state("This is a long enough summary explaining the niche clearly with detail.")
    out = await reviewer_node(state)
    assert out["brief"]["has_issues"] is False


async def test_reviewer_flags_short_summary():
    state = _brief_state("too short")
    out = await reviewer_node(state)
    assert out["brief"]["has_issues"] is True
    assert any("summary" in g.lower() for g in out["brief"]["gaps"])


async def test_reviewer_flags_placeholder_markers():
    state = _brief_state(
        "This brief is long enough to pass length but still includes a [INSERT TEXT] marker which is a placeholder."
    )
    out = await reviewer_node(state)
    assert out["brief"]["has_issues"] is True


async def test_reviewer_flags_missing_evidence():
    state = _brief_state(
        "This is a long enough summary explaining the niche clearly with detail.",
        evidence=[],
    )
    out = await reviewer_node(state)
    assert out["brief"]["has_issues"] is True
    assert any("evidence" in g.lower() for g in out["brief"]["gaps"])
```

- [ ] **Step 2: Run — confirm failure**

Run: `pytest tests/test_agent_nodes.py -v`
Expected: `ImportError: cannot import name 'reviewer_node'`.

- [ ] **Step 3: Implement**

Append to `app/agents/nodes.py`:

```python
_PLACEHOLDER_MARKERS = ("TODO", "[INSERT", "<placeholder>", "TBD")


async def reviewer_node(state: OpportunityState) -> OpportunityState:
    state.setdefault("errors", [])
    settings = get_settings()
    brief = state.get("brief") or {}
    gaps: list[str] = []

    summary = (brief.get("summary") or "").strip()
    if len(summary) < settings.brief_min_summary_chars:
        gaps.append(f"summary shorter than {settings.brief_min_summary_chars} chars")

    upper = summary.upper()
    for marker in _PLACEHOLDER_MARKERS:
        if marker.upper() in upper:
            gaps.append(f"summary contains placeholder '{marker}'")
            break

    if not brief.get("evidence"):
        gaps.append("no evidence items")

    brief["has_issues"] = bool(gaps)
    brief["gaps"] = gaps
    state["brief"] = brief

    if gaps:
        log.warning(
            "Brief has issues",
            component="reviewer_node",
            niche_id=state.get("niche", {}).get("id"),
            gaps=gaps,
        )
    else:
        log.info(
            "Brief reviewed clean",
            component="reviewer_node",
            niche_id=state.get("niche", {}).get("id"),
        )
    return state
```

- [ ] **Step 4: Run — confirm pass**

Run: `pytest tests/test_agent_nodes.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/agents/nodes.py tests/test_agent_nodes.py
git commit -m "feat(agents): add reviewer_node (heuristic completeness check)"
```

---

## Task 10 — Build the LangGraph graph + orchestrator + persistence (TDD)

Wires the five nodes into a linear `StateGraph`, exposes a `build_graph(adapter)` factory, and provides `run_brief_for_niche(niche_id, adapter, *, as_of, triggered_by)` which invokes the graph and persists the resulting `OpportunityBrief` (delete-then-insert per `(niche_id, date(generated_at))`).

**Files:**
- Create: `app/agents/graph.py`
- Modify: `app/agents/__init__.py` (re-export public API)
- Test: `tests/test_agent_graph_e2e.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_agent_graph_e2e.py`:

```python
from datetime import UTC, datetime

from sqlalchemy import select

from app.agents.graph import build_graph, run_brief_for_niche
from app.db import get_session, init_db
from app.features.signal_aggregator import aggregate_daily_signals
from app.llm.mock_adapter import MockLLMAdapter
from app.models import Niche, OpportunityBrief, SourceItem


async def _seed(slug: str = "alpha") -> tuple[int, datetime]:
    """Returns (niche_id, now). Returns now so each test uses the same `as_of`
    that was used to create the seed data — fetcher/retriever node cutoffs are
    `_utcnow()`-based, so we anchor to real now."""
    async with get_session() as session:
        n = Niche(slug=slug, name=slug.title(), category="devtools",
                  summary="seed", keywords_json=[slug])
        session.add(n)
        await session.commit()
        await session.refresh(n)
        nid = n.id

    now = datetime.now(UTC)
    async with get_session() as session:
        for i in range(3):
            session.add(SourceItem(
                source_type="github", external_id=f"g{i}",
                title=f"repo-{i}", body="useful body text",
                url=f"https://example.com/{i}",
                created_at=now, ingested_at=now,
                niche_id=nid, metadata_json={"stars": 50 + i},
            ))
        await session.commit()
    await aggregate_daily_signals(now)
    return nid, now


async def test_run_brief_for_niche_persists_brief():
    await init_db()
    nid, now = await _seed()

    brief_id = await run_brief_for_niche(
        nid, MockLLMAdapter(), as_of=now, triggered_by="scheduler"
    )

    assert brief_id is not None
    async with get_session() as session:
        rows = (await session.execute(
            select(OpportunityBrief).where(OpportunityBrief.niche_id == nid)
        )).scalars().all()
    assert len(rows) == 1
    brief = rows[0]
    assert brief.score_total is not None
    assert brief.headline
    assert brief.summary
    assert brief.evidence_json
    assert brief.forecast_label in ("Rising", "Stable", "Declining")
    assert brief.model_name == "MockLLMAdapter"
    assert isinstance(brief.score_breakdown_json, dict)


async def test_run_brief_for_niche_idempotent_same_day():
    await init_db()
    nid, now = await _seed()

    await run_brief_for_niche(nid, MockLLMAdapter(), as_of=now)
    await run_brief_for_niche(nid, MockLLMAdapter(), as_of=now)

    async with get_session() as session:
        rows = (await session.execute(
            select(OpportunityBrief).where(OpportunityBrief.niche_id == nid)
        )).scalars().all()
    assert len(rows) == 1


async def test_build_graph_can_be_invoked_directly():
    await init_db()
    nid, _ = await _seed()
    graph = build_graph(MockLLMAdapter())
    final_state = await graph.ainvoke({
        "niche": {"id": nid},
        "errors": [],
        "triggered_by": "command",
    })
    assert "brief" in final_state
    assert final_state["brief"]["headline"]


async def test_run_brief_marks_has_issues_when_summary_empty():
    """An adapter that returns empty summary → reviewer flags it; brief still persists."""
    await init_db()
    nid, now = await _seed()

    class EmptyAdapter(MockLLMAdapter):
        async def generate_brief(self, context):  # type: ignore[override]
            return ""

    await run_brief_for_niche(nid, EmptyAdapter(), as_of=now)

    async with get_session() as session:
        row = (await session.execute(
            select(OpportunityBrief).where(OpportunityBrief.niche_id == nid)
        )).scalar_one()
    assert row.has_issues is True
```

- [ ] **Step 2: Run — confirm failure**

Run: `pytest tests/test_agent_graph_e2e.py -v`
Expected: `ModuleNotFoundError: No module named 'app.agents.graph'` (the file is empty).

- [ ] **Step 3: Implement**

Replace contents of `app/agents/graph.py` (currently empty) with:

```python
"""LangGraph wiring for the opportunity-brief agent.

The graph is a linear pipeline (project doc §11):
    fetcher → retriever → forecaster → reporter → reviewer
The orchestrator `run_brief_for_niche` invokes the compiled graph and
persists the final state as an `OpportunityBrief` row.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph
from sqlalchemy import delete

from app.agents.nodes import (
    fetcher_node,
    forecaster_node,
    reporter_node,
    retriever_node,
    reviewer_node,
)
from app.agents.state import OpportunityState
from app.db import get_session
from app.llm.base import LLMAdapter
from app.models import OpportunityBrief

log = structlog.get_logger(__name__)


def build_graph(adapter: LLMAdapter):
    """Compile the linear opportunity-brief graph bound to `adapter`.

    The reporter is the only node that touches the LLM, so the adapter is
    captured in a closure here rather than passed through state.
    """
    sg: StateGraph = StateGraph(OpportunityState)

    async def _reporter(state: OpportunityState) -> OpportunityState:
        return await reporter_node(state, adapter=adapter)

    sg.add_node("fetcher", fetcher_node)
    sg.add_node("retriever", retriever_node)
    sg.add_node("forecaster", forecaster_node)
    sg.add_node("reporter", _reporter)
    sg.add_node("reviewer", reviewer_node)

    sg.add_edge(START, "fetcher")
    sg.add_edge("fetcher", "retriever")
    sg.add_edge("retriever", "forecaster")
    sg.add_edge("forecaster", "reporter")
    sg.add_edge("reporter", "reviewer")
    sg.add_edge("reviewer", END)

    return sg.compile()


def _start_of_day(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def _persist_brief(state: dict[str, Any], when: datetime) -> int:
    niche = state.get("niche") or {}
    brief = state.get("brief") or {}
    scorecard = state.get("scorecard") or {}
    day_start = _start_of_day(when)
    day_end = day_start + timedelta(days=1)

    async with get_session() as session:
        await session.execute(
            delete(OpportunityBrief).where(
                OpportunityBrief.niche_id == niche.get("id"),
                OpportunityBrief.generated_at >= day_start,
                OpportunityBrief.generated_at < day_end,
            )
        )
        row = OpportunityBrief(
            niche_id=niche["id"],
            headline=brief.get("headline"),
            summary=brief.get("summary"),
            score_total=scorecard.get("score_total"),
            score_breakdown_json=scorecard.get("breakdown"),
            evidence_json=brief.get("evidence"),
            forecast_label=brief.get("forecast_label"),
            has_issues=bool(brief.get("has_issues")),
            generated_at=when.astimezone(UTC) if when.tzinfo else when.replace(tzinfo=UTC),
            model_name=brief.get("model_name"),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def run_brief_for_niche(
    niche_id: int,
    adapter: LLMAdapter,
    *,
    as_of: datetime | None = None,
    triggered_by: str = "scheduler",
) -> int | None:
    """Run the full graph for one niche and persist an OpportunityBrief.

    Returns the new OpportunityBrief.id, or None if persistence was skipped
    because the graph couldn't produce a brief (e.g. niche missing).
    """
    when = as_of or datetime.now(UTC)
    graph = build_graph(adapter)
    initial: OpportunityState = {
        "niche": {"id": niche_id},
        "errors": [],
        "triggered_by": triggered_by,
    }
    final = await graph.ainvoke(initial)

    if not final.get("brief") or not (final.get("niche") or {}).get("id"):
        log.warning(
            "Skipping persistence — incomplete state",
            component="agent_orchestrator",
            niche_id=niche_id,
            errors=final.get("errors", []),
        )
        return None

    brief_id = await _persist_brief(final, when)
    log.info(
        "Brief persisted",
        component="agent_orchestrator",
        niche_id=niche_id,
        brief_id=brief_id,
        has_issues=final["brief"].get("has_issues"),
        triggered_by=triggered_by,
    )
    return brief_id
```

Update `app/agents/__init__.py` (currently empty):

```python
from app.agents.graph import build_graph, run_brief_for_niche
from app.agents.state import OpportunityState

__all__ = ["build_graph", "run_brief_for_niche", "OpportunityState"]
```

- [ ] **Step 4: Run — confirm pass**

Run: `pytest tests/test_agent_graph_e2e.py -v`
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/agents/graph.py app/agents/__init__.py tests/test_agent_graph_e2e.py
git commit -m "feat(agents): wire LangGraph + orchestrator + brief persistence"
```

---

## Task 11 — Wire daily brief generation into the scheduler

Mirrors the existing `daily_scoring` job at `app/ingestion/scheduler.py:40-61`. Runs after scoring (default 03:00 UTC), iterates every niche, calls `run_brief_for_niche` per niche. The job picks the LLM adapter based on `settings.llm_provider`.

**Files:**
- Modify: `app/ingestion/scheduler.py`

- [ ] **Step 1: Edit scheduler**

In `app/ingestion/scheduler.py`:

1. Add imports at the top (after the existing `from app.forecasting.scoring import score_all_niches` line at line 11):

```python
from sqlalchemy import select

from app.agents.graph import run_brief_for_niche
from app.db import get_session
from app.llm.base import LLMAdapter
from app.llm.mock_adapter import MockLLMAdapter
from app.llm.ollama_adapter import OllamaAdapter
from app.models import Niche
```

2. Add a helper function above `build_scheduler(...)` (around line 16):

```python
def _select_adapter(settings: Settings) -> LLMAdapter:
    if settings.llm_provider == "ollama":
        return OllamaAdapter(base_url=settings.ollama_base_url, model=settings.ollama_model)
    return MockLLMAdapter()
```

3. Inside `build_scheduler(...)`, after the existing `daily_scoring` job registration (after line 61), add:

```python
    async def _brief_job():
        adapter = _select_adapter(settings)
        try:
            async with get_session() as session:
                niche_ids = (await session.execute(select(Niche.id))).scalars().all()
            generated = 0
            for nid in niche_ids:
                try:
                    if await run_brief_for_niche(nid, adapter, triggered_by="scheduler"):
                        generated += 1
                except Exception as exc:
                    log.error(
                        "Brief job: niche failed",
                        component="scheduler",
                        niche_id=nid,
                        error=str(exc),
                    )
            log.info(
                "Daily brief generation complete",
                component="scheduler",
                niches_total=len(niche_ids),
                briefs_generated=generated,
            )
        except Exception as exc:
            log.error("Daily brief generation failed", component="scheduler", error=str(exc))

    scheduler.add_job(
        _brief_job,
        CronTrigger(hour=settings.brief_cron_hour, minute=settings.brief_cron_minute),
        id="daily_brief_generation",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
```

4. Update the final `log.info("Scheduler built", ...)` call to include the new job:

```python
    log.info(
        "Scheduler built",
        component="scheduler",
        jobs=list(connector_map.keys()) + ["daily_scoring", "daily_brief_generation"],
    )
```

- [ ] **Step 2: Verify imports**

Run: `python -c "from app.ingestion.scheduler import build_scheduler; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Verify scheduler builds without ollama running**

Run:
```bash
LLM_PROVIDER=mock python -c "
import asyncio
from app.config import get_settings
from app.ingestion.base import ConnectorRunRegistry
from app.ingestion.scheduler import build_scheduler
s = build_scheduler([], ConnectorRunRegistry(), get_settings())
print('jobs:', [j.id for j in s.get_jobs()])
"
```
Expected output includes `daily_scoring` and `daily_brief_generation`.

- [ ] **Step 4: Commit**

```bash
git add app/ingestion/scheduler.py
git commit -m "feat(agents): schedule daily opportunity brief generation"
```

---

## Task 12 — End-to-end smoke script

**Files:**
- Create: `scripts/run_agent.py`

- [ ] **Step 1: Create script**

Create `scripts/run_agent.py`:

```python
"""Manual smoke-test: run the agent graph against the live DB.

Usage:
  python scripts/run_agent.py            # Runs for all niches with MockLLMAdapter
  python scripts/run_agent.py --ollama   # Use OllamaAdapter (requires running Ollama + qwen2.5)
  python scripts/run_agent.py --niche ai-habit-trackers
"""
import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import get_session, init_db
from app.agents.graph import run_brief_for_niche
from app.features.niche_builder import sync_niches_from_yaml
from app.llm.mock_adapter import MockLLMAdapter
from app.llm.ollama_adapter import OllamaAdapter
from app.models import Niche


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ollama", action="store_true", help="Use OllamaAdapter")
    parser.add_argument("--niche", help="Specific niche slug (default: all)")
    args = parser.parse_args()

    await init_db()
    await sync_niches_from_yaml(Path("data/niches.yaml"))

    if args.ollama:
        s = get_settings()
        adapter = OllamaAdapter(base_url=s.ollama_base_url, model=s.ollama_model)
    else:
        adapter = MockLLMAdapter()

    async with get_session() as session:
        stmt = select(Niche.id, Niche.slug)
        if args.niche:
            stmt = stmt.where(Niche.slug == args.niche)
        niches = (await session.execute(stmt)).all()

    now = datetime.now(UTC)
    for nid, slug in niches:
        try:
            brief_id = await run_brief_for_niche(nid, adapter, as_of=now, triggered_by="command")
            print(f"  {slug}: brief_id={brief_id}")
        except Exception as exc:
            print(f"  {slug}: FAILED — {exc}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify with Mock adapter**

Run (after at least one ingestion + scoring run; otherwise first run `python scripts/seed_mock_data.py && python scripts/run_ingestion.py && python scripts/run_scoring.py`):

```bash
python scripts/run_agent.py
```

Expected: prints one `<slug>: brief_id=<id>` line per niche, no traceback.

- [ ] **Step 3: Commit**

```bash
git add scripts/run_agent.py
git commit -m "chore(agents): add manual run_agent smoke script"
```

---

## Task 13 — ADR-005, KANBAN, final sweep

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all M1/M2/M3 tests still pass + the new M4 test files all pass. Fix any regression before continuing.

- [ ] **Step 2: Append ADR-005**

Append to `docs/decisions.md`:

```markdown
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
```

- [ ] **Step 3: Flip KANBAN status**

In `KANBAN.md`, set `Status` to `Done` for: M4-01, M4-02, M4-03, M4-04, M4-05, M4-06, M4-07, M4-08, M4-09, M4-10.

- [ ] **Step 4: Commit**

```bash
git add docs/decisions.md KANBAN.md
git commit -m "docs(m4): mark M4 tasks done; add ADR-005 (agent graph)"
```

---

## Verification (end-to-end)

1. **Unit tests (new only)** — `pytest tests/test_agent_graph.py tests/test_ollama_adapter.py tests/test_agent_nodes.py tests/test_agent_graph_e2e.py -v` → all green.
2. **Full suite regression** — `pytest -v` → no M1/M2/M3 tests broken.
3. **Smoke run with mock adapter:**
   ```bash
   python scripts/seed_mock_data.py
   python scripts/run_ingestion.py
   python scripts/run_scoring.py
   python scripts/run_agent.py
   ```
   Expected: prints one `<slug>: brief_id=<id>` per niche.
4. **DB inspection:**
   ```bash
   sqlite3 devtrend.db "SELECT niche_id, score_total, has_issues, model_name, headline FROM opportunity_briefs ORDER BY generated_at DESC LIMIT 10;"
   ```
   Expected: one row per niche for today; `model_name = 'MockLLMAdapter'`; `has_issues` mostly 0; non-empty headlines.
5. **Scheduler boot:**
   ```bash
   LLM_PROVIDER=mock uvicorn app.main:app
   ```
   Expected boot log includes `"Scheduler built" ... jobs=[..., "daily_scoring", "daily_brief_generation"]`. Brief job fires at `03:00 UTC` (configurable via `BRIEF_CRON_HOUR` / `BRIEF_CRON_MINUTE`).
6. **Optional Ollama smoke (only if `ollama serve` is running with `qwen2.5`):**
   ```bash
   python scripts/run_agent.py --ollama --niche <one-slug>
   ```
   Expected: brief is generated using qwen2.5; `model_name = 'OllamaAdapter'` in DB.

---

## Out of scope (intentionally deferred)

- **`/briefing`, `/niche <slug>` bot commands** — M5-01 / M5-03 will read `OpportunityBrief` rows produced here.
- **Daily digest push** — M5-06 reads the top-3 briefs and pushes via Telegram.
- **Spike alert push** — M5-07 compares `NicheScoreHistory` deltas, not briefs.
- **Reviewer self-correction loop / retries** — Phase 2 (LangGraph conditional edges).
- **OpenAI / Anthropic adapters** — Phase 2 swap-ins; the abstract base is already in place.
- **JSON output schema for the LLM brief** — deliberately avoided in Phase 1; revisit if Ollama fidelity proves insufficient.
- **Streaming brief generation** — not needed; daily batch.

---

## KANBAN coverage

| Kanban ID | Covered by |
|---|---|
| M4-01 `OpportunityState` TypedDict | Task 2 |
| M4-02 LangGraph graph skeleton | Task 10 (`build_graph`) |
| M4-03 `fetcher_node` | Task 5 |
| M4-04 `retriever_node` | Task 6 |
| M4-05 `forecaster_node` | Task 7 (reads NicheScoreHistory; cold-start fallback to `score_niche`) |
| M4-06 Ollama adapter | Task 4 (`OllamaAdapter` + prompt templates from Task 3) |
| M4-07 `reporter_node` (90s timeout) | Task 8 |
| M4-08 `reviewer_node` (validate-only, no retry) | Task 9 |
| M4-09 Brief persistence with denormalised JSON | Task 10 (`_persist_brief` in orchestrator) |
| M4-10 Scheduler: brief job (`max_instances=1`) | Task 11 |
