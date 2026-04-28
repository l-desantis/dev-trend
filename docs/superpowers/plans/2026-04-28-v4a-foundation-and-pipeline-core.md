# Plan — v4.A: Foundation & Pipeline Core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Environment note:** This project runs on Windows/WSL2 with `uv`. The implementer cannot run `uv`/`python`/`pytest` directly — every "Run:" line in this plan should be handed to the user (`! uv run ...`) for them to execute and paste back.
>
> **Git note:** No direct git commits by the assistant. Each task ends with a suggested commit message and `git add` set; the user will commit when ready.

**Goal:** Replace the v3 niche-scoring pipeline with the v4 opportunity-discovery pipeline up to candidate creation. End state: a fresh DB run produces `OpportunityCandidate` rows with attached `PainPoint` evidence; no scoring, no UI, no validation yet (those are Plan B).

**Architecture:** Drop v3 schema and replace with v4. Add a 5-stage pipeline (extract → embed → identity-resolve → cluster → label) running on the daily cron, plus a provider-independent `run_backfill.py` CLI for high-volume one-shot extraction via local Ollama. Adapters for LLM and embeddings select Ollama / NIM / Mock at startup via `LLM_PROVIDER` env var.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.x ORM (existing), Pydantic v2, Ollama (`qwen2.5` + `nomic-embed-text`) for dev, NVIDIA NIM (config-only here, no client until Plan B/C), `numpy`, `scikit-learn` (HDBSCAN via `hdbscan` package or fallback `KMeans`).

**Spec reference:** `docs/superpowers/specs/2026-04-28-opportunity-discovery-pivot-design.md`

---

## Context

M6 just shipped — Phase 1 (v3) is complete and stable. The pivot brief decided in brainstorming: clean break to v4, no parallel pipelines. Plan A delivers schema + ingestion role-tagging + the inference pipeline up to candidate creation. The bot is intentionally degraded during Plan A — only `/start`, `/help`, `/sources` work; v3 commands (`/briefing`, `/niches`, etc.) are removed, v4 commands arrive in Plan B. This is acceptable because deployment is local-only with no users.

Two cross-cutting choices reused across this plan:

- **Adapter DI pattern from M4** — adapters are picked at startup in `app/llm/factory.py` and injected into pipeline stages. No globals.
- **Connector base + scheduler patterns from M2/M5.5** — pipeline stages plug into the same `AsyncIOScheduler` already wired by M5/M5.5.

---

## File-Level Plan

**New files:**
- `app/models.py` — modified: drop v3 entities, add v4 entities
- `data/categories.yaml` — replaces `data/niches.yaml`
- `app/db_helpers/categories.py` — `sync_categories_from_yaml()`
- `app/llm/embedding_base.py` — `EmbeddingAdapter` ABC + Pydantic schemas
- `app/llm/ollama_embedding_adapter.py`
- `app/llm/mock_embedding_adapter.py`
- `app/llm/factory.py` — `make_llm_adapter()`, `make_embedding_adapter()`
- `app/llm/schemas.py` — `PainPointDraft`, `ClusterLabel`
- `app/pipeline/__init__.py`
- `app/pipeline/extract.py` — Stage 1
- `app/pipeline/embed.py` — Stage 2
- `app/pipeline/embedding_index.py` — numpy cosine helper
- `app/pipeline/identity_resolution.py` — Stage 3
- `app/pipeline/clustering.py` — Stage 4
- `app/pipeline/labelling.py` — Stage 5 + category assignment
- `app/pipeline/orchestrator.py` — `run_pipeline()` entrypoint
- `scripts/migrate_to_v4.py` — one-shot DB migration
- `scripts/run_backfill.py` — provider-independent backfill CLI
- Tests under `tests/pipeline/`, `tests/llm/`, `tests/test_categories.py`

**Modified:**
- `app/llm/base.py` — extend `LLMAdapter` ABC with `extract_pain_point`, `label_cluster`
- `app/llm/ollama_adapter.py` — implement new methods
- `app/llm/mock_adapter.py` — implement new methods
- `app/ingestion/reddit_connector.py` — set `role='extraction'` on each item
- `app/ingestion/github_connector.py` — set `role='validation'`
- `app/ingestion/hn_connector.py` — split: Ask HN + comments → `extraction`; Show HN → `validation`; news → `ignored`
- `app/ingestion/scheduler.py` — remove v3 jobs (scoring, brief, digest, spike); add daily pipeline cron
- `app/ingestion/backfill.py` — call orchestrator after ingestion phase
- `app/bot/handlers.py` — strip v3 command handlers (keep `/start`, `/help`, `/sources`)
- `app/bot/scheduler_hooks.py` — strip digest/spike push hooks (Plan B will replace)
- `app/main.py` lifespan — call `sync_categories_from_yaml()` after `create_all`
- `app/config.py` + `.env.example` — add v4 config keys (see A-04 below)
- `pyproject.toml` — add `numpy`, `scikit-learn`, `hdbscan`, `httpx` (httpx already present)

**Removed (this plan):**
- v3 jobs from `app/ingestion/scheduler.py`
- v3 command handlers from `app/bot/handlers.py`
- Active references to `Niche`, `NicheSignal`, `NicheScoreHistory`, `OpportunityBrief` in models and ORM

**Removed (deferred to Plan C — keeps Plan A diff smaller):**
- `app/agents/` directory (LangGraph code)
- `app/forecasting/scoring.py`
- `app/ingestion/appstore_mock_connector.py`
- `data/niches.yaml`
- `data/mock/` JSON files

---

## Tasks

### A-01 — v4 ORM models

**Files:** `app/models.py` (rewrite), `tests/test_models.py` (extend)

Replace `Niche`, `NicheSignal`, `NicheScoreHistory`, `OpportunityBrief` with the v4 entities defined in spec §4.1. Keep `MaintenanceState` and `SourceItem` (modified). Add `Category`, `PainPoint`, `OpportunityCandidate`, `CandidateValidation`, `CandidateScoreHistory`, `CandidateBrief`, `CandidateFeedback`.

`SourceItem` modifications:
- Drop `niche_id` and the `niche` relationship.
- Add: `category_id: Mapped[int | None] = mapped_column(ForeignKey('categories.id'), index=True)` — nullable, populated by labelling stage when a SourceItem's PainPoint is attached to a candidate (the candidate's category propagates back via a SQL `UPDATE source_items SET category_id = :cat WHERE id IN (...)` once at the end of stage 5).
- Add: `role: Mapped[str] = mapped_column(String(20), nullable=False, default='extraction', index=True)` — `'extraction' | 'validation' | 'ignored'`.
- Add: `extraction_state: Mapped[str] = mapped_column(String(20), nullable=False, default='pending', index=True)` — `'pending' | 'extracted' | 'no_signal' | 'failed'`.

`PainPoint` indexes: `(source_item_id)`, `(candidate_id)`, `(extracted_at)`. `PainPoint.source_item_id` FK uses `ondelete='CASCADE'` so the M6 pruning job (which deletes SourceItems > 90 days old) automatically drops orphaned PainPoints — preserves spec §4.4's pruning semantics. `OpportunityCandidate` indexes: `(category_id)`, `(lifecycle_state)`, `(is_archived)`. Embedding stored as `JSON` (list[float]) — cheapest portable option, compatible with NumPy after a single `np.asarray()` call.

`CandidateFeedback` `UNIQUE (candidate_id, user_id, brief_id)` — `brief_id` nullable; treat NULL as a sentinel via SQLAlchemy `UniqueConstraint` with explicit `sqlite_where=` (SQLite uniqueness with NULL is platform-dependent; document the gotcha).

**Tests in this task:**
- `test_models_can_create_each_v4_entity` — instantiate each class, assert FK relationships work
- `test_source_item_default_role_is_extraction`
- `test_candidate_unique_feedback_constraint` — same `(candidate_id, user_id, brief_id)` insert raises `IntegrityError`

**Suggested commit:** `refactor(models): drop v3 entities, add v4 candidate/painpoint schema`

---

### A-02 — DB migration script

**Files:** `scripts/migrate_to_v4.py`, `tests/test_migrate_to_v4.py`

Stand-alone CLI script. Drops v3 tables and creates v4 tables in one shot. Not Alembic — keeps the project's "no-migrations-framework" stance.

```python
# scripts/migrate_to_v4.py (skeleton — full code in implementation)
import argparse, asyncio
from sqlalchemy import inspect, text
from app.db import engine, Base
from app import models  # ensures all v4 models registered on Base.metadata

V3_TABLES = ["opportunity_briefs", "niche_score_history", "niche_signals", "niches"]

async def migrate(confirm: bool) -> None:
    if not confirm:
        raise SystemExit("Refusing to run without --confirm")
    async with engine.begin() as conn:
        existing = await conn.run_sync(lambda c: {t for t in inspect(c).get_table_names()})
        for table in V3_TABLES:
            if table in existing:
                await conn.execute(text(f"DROP TABLE {table}"))
        await conn.run_sync(Base.metadata.create_all)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--confirm", action="store_true")
    asyncio.run(migrate(p.parse_args().confirm))
```

`MaintenanceState.last_pruned_at` value is preserved (table is not dropped). New v4 tables come up empty.

**Tests:**
- `test_migrate_drops_v3_creates_v4` — using an in-memory SQLite, seed v3 tables manually, run migration, assert v3 tables gone and v4 tables present.
- `test_migrate_refuses_without_confirm` — assert `SystemExit`.
- `test_migrate_idempotent` — running twice should not error (second run is a no-op for table drops; `create_all` is idempotent).

**Suggested commit:** `feat(scripts): add migrate_to_v4 one-shot DB migration`

---

### A-03 — Categories YAML + sync

**Files:** `data/categories.yaml`, `app/db_helpers/categories.py`, `tests/test_categories.py`

Replace `data/niches.yaml` (kept on disk for now — Plan C deletes it) with `data/categories.yaml`:

```yaml
categories:
  - slug: wellness
    name: Wellness & Mental Health
    description: Apps for habit, fitness, sleep, mental wellness, nutrition.
  - slug: finance
    name: Personal Finance
    description: Budgeting, expense tracking, subscription management, net-worth.
  - slug: devtools
    name: Developer Tools
    description: Code review, observability, CI/CD, IDEs, infrastructure tooling.
  - slug: productivity
    name: Productivity & Knowledge
    description: PKM, async collaboration, time tracking, writing assistants.
  - slug: creative
    name: Creative & Media
    description: AI-assisted image/audio/video generation and editing.
  - slug: gaming
    name: Gaming & Indie Games
    description: Game engines, asset pipelines, indie publishing tools.
```

`app/db_helpers/categories.py`:

```python
async def sync_categories_from_yaml(session: AsyncSession, path: Path = ...) -> None:
    """Read categories.yaml, upsert each row into the categories table.
    Existing rows are updated by slug; missing rows are not deleted (preserve FK integrity)."""
```

Idempotent — same shape as the existing `sync_niches_from_yaml` (look it up in `app/db_helpers/niches.py` if it exists, or grep `niches.yaml`). Logs `categories_synced count=N` once.

**Tests:**
- `test_sync_categories_inserts_new` — empty DB → 6 rows.
- `test_sync_categories_updates_existing_by_slug` — pre-seed `wellness` with old name, run sync, assert name updated.
- `test_sync_categories_does_not_delete_missing` — pre-seed `legacy_slug` not in YAML, run sync, assert row still present.

**Suggested commit:** `feat(categories): replace niches.yaml with categories.yaml + sync`

---

### A-04 — Config additions + lifespan wiring

**Files:** `app/config.py`, `.env.example`, `app/main.py`

Add to `Settings`:

```python
llm_provider: Literal["ollama", "nim", "mock"] = "ollama"
embedding_provider: Literal["ollama", "nim", "mock"] = "ollama"

nim_api_key: str = ""
nim_llm_model: str = "meta/llama-3.1-70b-instruct"
nim_embedding_model: str = "nvidia/nv-embedqa-e5-v5"

extraction_batch_size: int = 20
embedding_batch_size: int = 64
identity_resolution_threshold: float = 0.82
clustering_min_cluster_size: int = 3
specificity_gate: int = 2
max_alerts_per_day: int = 3   # used by Plan B; declare here so .env stays stable

weekly_recluster_cron_hour: int = 4
weekly_recluster_cron_day: str = "sun"

playstore_top_n_per_category: int = 50    # used by Plan C
playstore_reviews_per_app: int = 200
```

Mirror in `.env.example` with comments.

**Lifespan changes (`app/main.py`):** after `Base.metadata.create_all` (or instead of, depending on existing pattern), call `await sync_categories_from_yaml(session)`. Remove any call to `sync_niches_from_yaml` (kept on disk for Plan C; just not called).

**Tests:** existing `test_config_loads_env` should still pass; add a `test_config_v4_defaults` asserting the new fields have the documented defaults.

**Suggested commit:** `feat(config): add v4 provider + pipeline settings`

---

### A-05 — Connector role tagging

**Files:** `app/ingestion/reddit_connector.py`, `app/ingestion/github_connector.py`, `app/ingestion/hn_connector.py`, `tests/test_connectors.py`

Each connector's `normalize()` method writes the new `role` (and leaves `category_id` NULL, `extraction_state='pending'`).

- **Reddit** — every item gets `role='extraction'`.
- **GitHub** — every item gets `role='validation'`.
- **HN** — split:
  - Title starts with `"Show HN:"` → `role='validation'`
  - Title starts with `"Ask HN:"` OR `metadata_json['_tags']` contains `"comment"` → `role='extraction'`
  - Else → `role='ignored'`

The HN ingestion should not skip ignored items at fetch time — they're still stored (cheap, useful as ambient context) but `role='ignored'` ensures stage 1 skips them. This is a deliberate choice: it's easier to broaden the extraction filter later than to backfill stories we discarded.

**Tests:**
- `test_reddit_normalizes_role_extraction`
- `test_github_normalizes_role_validation`
- `test_hn_normalizes_role_split` — three fixtures: Ask HN title, Show HN title, plain news; assert roles.

**Suggested commit:** `feat(ingestion): role-tag SourceItems per source type`

---

### A-06 — Pydantic schemas for LLM I/O

**Files:** `app/llm/schemas.py`, `tests/llm/test_schemas.py`

```python
class PainPointDraft(BaseModel):
    has_unmet_need: bool
    problem_text: str | None = None    # required when has_unmet_need
    audience: str | None = None
    urgency_cue: str | None = None
    current_workaround: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "PainPointDraft":
        if self.has_unmet_need and not (self.problem_text and self.audience):
            raise ValueError("problem_text and audience required when has_unmet_need is True")
        return self


class ClusterLabel(BaseModel):
    problem_statement: str
    audience: str
    why_now: str
    specificity: int = Field(ge=1, le=5)
    suggested_category_slug: str | None = None
```

**Tests:**
- `test_painpoint_draft_validates_coherence` — has_unmet_need=True with empty problem_text raises.
- `test_painpoint_draft_no_signal_passes` — has_unmet_need=False with all-None fields is valid.
- `test_cluster_label_specificity_bounds` — 0 and 6 raise.

**Suggested commit:** `feat(llm): add PainPointDraft + ClusterLabel schemas`

---

### A-07 — Extend LLMAdapter ABC

**Files:** `app/llm/base.py`, `tests/llm/test_adapter_interface.py`

Extend the existing `LLMAdapter` ABC:

```python
class LLMAdapter(ABC):
    # existing v3 method, kept for now (will be deleted in Plan C with the agent code)
    async def generate_brief(self, ...) -> str: ...

    # new v4 methods
    @abstractmethod
    async def extract_pain_point(
        self, source_item_text: str, *, model_hint: str | None = None
    ) -> PainPointDraft: ...

    @abstractmethod
    async def label_cluster(
        self, evidence_texts: list[str], category_slugs: list[str]
    ) -> ClusterLabel: ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Stable model identifier used as the extraction cache key (e.g. 'qwen2.5')."""
```

**Test (interface compliance):** `test_concrete_adapters_implement_v4_methods` — for each concrete adapter, assert the methods exist and the ABC raises if any concrete subclass forgets one.

**Suggested commit:** `feat(llm): extend LLMAdapter with extract_pain_point + label_cluster`

---

### A-08 — `OllamaAdapter` v4 methods

**Files:** `app/llm/ollama_adapter.py`, `app/agents/prompts.py` (add new prompt templates — keep file even though Plan C will eventually relocate it; pragmatic for this plan), `tests/llm/test_ollama_adapter.py`

Two new methods. Both call `qwen2.5` via the existing `httpx` async client and parse a JSON response.

**`extract_pain_point` prompt template (new in `prompts.py`):**

```
You analyse a single piece of developer / market chatter and decide whether it
contains an unmet-need signal that could justify a new app.

Input text:
---
{text}
---

Return STRICT JSON with these keys:
- has_unmet_need: boolean
- problem_text: string (1 sentence, only if has_unmet_need=true; else "")
- audience: string (1 phrase, only if has_unmet_need=true; else "")
- urgency_cue: string (e.g. "repeated complaint", "specific deadline", "explicit ask"; "" if none)
- current_workaround: string ("" if not mentioned)

Examples of HIGH-signal text: complaints, "I wish there was an app that...",
"why is there no good X", repeated requests in a thread.
Examples of LOW-signal text: news headlines, tech announcements, marketing posts,
generic discussion. For these, set has_unmet_need=false and leave the strings
empty.

Reply with ONLY the JSON object, no prose.
```

Implementation:

```python
async def extract_pain_point(self, source_item_text: str, *, model_hint=None) -> PainPointDraft:
    prompt = EXTRACT_PROMPT.format(text=source_item_text[:4000])
    raw = await self._chat(prompt, model=model_hint or self._model, format="json")
    try:
        return PainPointDraft.model_validate_json(raw)
    except (ValidationError, json.JSONDecodeError) as e:
        log.warning("extract_pain_point_invalid_json", error=str(e), raw=raw[:200])
        return PainPointDraft(has_unmet_need=False)  # safe fallback: skip this item
```

The `format="json"` parameter to Ollama's `/api/chat` endpoint forces JSON output (Ollama feature) — this is the v3 pattern in `_chat`; reuse it. Falling back to `has_unmet_need=False` on validation failure means a malformed extraction is silently dropped, which is the right behaviour (we don't want to halt the pipeline on one bad LLM response).

**`label_cluster` prompt template:**

```
You are labelling a cluster of pain points extracted from developer & user
chatter. Produce a concrete app-opportunity hypothesis.

Cluster evidence (one item per line):
{evidence_lines}

Available categories: {categories}

Return STRICT JSON with these keys:
- problem_statement: 1 sentence describing the opportunity (mid-precision —
  specific enough to be actionable, broad enough to allow exploration).
- audience: 1 phrase describing who has the problem.
- why_now: 1 sentence on what makes this timely (e.g. tech enabler, emerging
  workflow, repeated recent mention).
- specificity: integer 1–5. 5 = a concrete app idea with clear scope; 1 = vague,
  could mean many different products. Be honest — vague clusters get filtered.
- suggested_category_slug: one of the available categories, or null.

Reply with ONLY the JSON object, no prose.
```

**Tests (with `httpx_mock` fixtures, same pattern as `test_connectors.py`):**

- `test_extract_pain_point_returns_draft` — mock Ollama to return valid JSON, assert parsed shape.
- `test_extract_pain_point_invalid_json_falls_back_to_no_signal`
- `test_extract_pain_point_truncates_long_text` — input >4000 chars; assert prompt sent to Ollama is truncated.
- `test_label_cluster_returns_label`
- `test_label_cluster_specificity_clamped` — Ollama returns 7; expect ValidationError → some sensible fallback (suggest: log + raise; the orchestrator will fail-fast on a single bad cluster, but that's loud rather than silent which is what we want for the user-facing labelling).

**Suggested commit:** `feat(llm): implement OllamaAdapter.extract_pain_point + label_cluster`

---

### A-09 — `MockLLMAdapter` v4 methods

**Files:** `app/llm/mock_adapter.py`, `tests/llm/test_mock_adapter.py`

Deterministic fixture behaviour for tests:

```python
class MockLLMAdapter(LLMAdapter):
    @property
    def model_name(self) -> str:
        return "mock-llm-v1"

    async def extract_pain_point(self, source_item_text, *, model_hint=None):
        # Heuristic: text containing "wish there was" / "why is there no" / "?"
        # in an ASK_HN-style title becomes a pain point. Else no_signal.
        if any(kw in source_item_text.lower() for kw in ["wish", "why is there no", "should be a way to"]):
            return PainPointDraft(
                has_unmet_need=True,
                problem_text=f"User wants: {source_item_text[:80]}",
                audience="users mentioned in the text",
                urgency_cue="repeated complaint",
            )
        return PainPointDraft(has_unmet_need=False)

    async def label_cluster(self, evidence_texts, category_slugs):
        # Concatenate first words of each evidence; specificity rises with cluster size.
        first_words = " · ".join(t.split(".")[0][:30] for t in evidence_texts[:3])
        return ClusterLabel(
            problem_statement=f"Cluster: {first_words}",
            audience="mocked audience",
            why_now="mocked why-now",
            specificity=min(5, max(1, len(evidence_texts) // 2)),
            suggested_category_slug=(category_slugs[0] if category_slugs else None),
        )
```

**Tests:** assert deterministic outputs given the same input.

**Suggested commit:** `feat(llm): MockLLMAdapter v4 fixture behaviour`

---

### A-10 — EmbeddingAdapter ABC + Ollama + Mock

**Files:** `app/llm/embedding_base.py`, `app/llm/ollama_embedding_adapter.py`, `app/llm/mock_embedding_adapter.py`, `tests/llm/test_embedding_adapters.py`

```python
class EmbeddingAdapter(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def dim(self) -> int: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...
```

`OllamaEmbeddingAdapter` calls `POST {base_url}/api/embeddings` with `model='nomic-embed-text'`. Batches input by sending one request per text — Ollama's embeddings endpoint historically only accepts a single input per call; iterate. (Verify before implementing — Ollama added batch support in late 2024; if available, batch in chunks of `embedding_batch_size`.) `dim` is hard-coded to 768 for `nomic-embed-text` (sanity-check the first call's response and assert it matches).

`MockEmbeddingAdapter` returns a deterministic vector derived from `hash(text)`:

```python
def _vec(text: str, dim: int = 32) -> list[float]:
    h = hashlib.sha256(text.encode()).digest()
    # Map bytes to floats in [-1, 1], pad/truncate to dim.
    raw = [(b - 128) / 128 for b in h]
    return (raw * (dim // len(raw) + 1))[:dim]
```

`dim=32` is fine for tests — fast cosine math, deterministic.

**Tests:**
- `test_ollama_embedding_returns_vector_of_expected_dim` — mock `httpx_mock`, assert dim 768.
- `test_mock_embedding_deterministic` — same text → same vector.
- `test_mock_embedding_different_texts_differ` — sanity.

**Suggested commit:** `feat(llm): add EmbeddingAdapter (Ollama + Mock)`

---

### A-11 — Adapter factory

**Files:** `app/llm/factory.py`, `tests/llm/test_factory.py`

```python
def make_llm_adapter(settings: Settings) -> LLMAdapter:
    match settings.llm_provider:
        case "ollama": return OllamaAdapter(base_url=settings.ollama_base_url, model=settings.ollama_model)
        case "mock":   return MockLLMAdapter()
        case "nim":    raise NotImplementedError("NIM adapter lands in Plan C")
        case _:        raise ValueError(f"unknown llm_provider: {settings.llm_provider}")

def make_embedding_adapter(settings: Settings) -> EmbeddingAdapter:
    match settings.embedding_provider:
        case "ollama": return OllamaEmbeddingAdapter(base_url=settings.ollama_base_url, model="nomic-embed-text")
        case "mock":   return MockEmbeddingAdapter()
        case "nim":    raise NotImplementedError("NIM embedding adapter lands in Plan C")
        case _:        raise ValueError(f"unknown embedding_provider: {settings.embedding_provider}")
```

Plan A doesn't ship NIM adapters. The `nim` case is wired with `NotImplementedError` so the factory contract is complete and Plan C drops in the implementation without changing call sites.

**Tests:** one happy path per provider, plus `nim` raises `NotImplementedError`.

**Suggested commit:** `feat(llm): adapter factory selecting by provider env`

---

### A-12 — `EmbeddingIndex` (NumPy cosine)

**Files:** `app/pipeline/embedding_index.py`, `tests/pipeline/test_embedding_index.py`

A thin interface so Plan B/C can swap to sqlite-vec / pgvector without changing call sites:

```python
class EmbeddingIndex:
    """Brute-force cosine similarity over an in-memory matrix.
    Suitable for <10k vectors. Recompute on each pipeline run."""

    def __init__(self, ids: list[int], vectors: list[list[float]]):
        self._ids = np.asarray(ids, dtype=np.int64)
        m = np.asarray(vectors, dtype=np.float32)
        # Pre-normalise for cosine = dot product
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        self._matrix = m / np.where(norms == 0, 1, norms)

    def nearest(self, query: list[float], k: int = 1, threshold: float = 0.0) -> list[tuple[int, float]]:
        q = np.asarray(query, dtype=np.float32)
        qn = q / (np.linalg.norm(q) or 1.0)
        sims = self._matrix @ qn
        idx = np.argsort(-sims)[:k]
        return [(int(self._ids[i]), float(sims[i])) for i in idx if sims[i] >= threshold]

    def __len__(self) -> int: return len(self._ids)
```

**Tests:**
- `test_index_nearest_returns_self_at_top` — index a vector and query with the same vector; expect sim ≈ 1.0.
- `test_index_threshold_filters` — query against orthogonal vector with threshold 0.5; expect empty.
- `test_index_empty` — `EmbeddingIndex([], [])` — `nearest()` returns `[]`.
- `test_index_handles_zero_vector` — zero vector doesn't blow up; sims defined.

**Suggested commit:** `feat(pipeline): NumPy EmbeddingIndex with cosine similarity`

---

### A-13 — Stage 1: extract.py

**Files:** `app/pipeline/extract.py`, `tests/pipeline/test_extract.py`

```python
async def run_extraction(
    session: AsyncSession,
    llm: LLMAdapter,
    *,
    since: datetime | None = None,
    force: bool = False,
    batch_size: int = 20,
) -> ExtractionReport:
    """Stage 1.

    Selects SourceItems where:
        role == 'extraction'
        AND extraction_state == 'pending'
        AND (since is None OR ingested_at >= since)

    For each, calls llm.extract_pain_point(text). On has_unmet_need=False:
        update SourceItem.extraction_state = 'no_signal'
    On has_unmet_need=True:
        insert PainPoint(source_item_id=..., extractor_model=llm.model_name, ...)
        update SourceItem.extraction_state = 'extracted'
    On exception:
        update SourceItem.extraction_state = 'failed'
        log structured error

    Cache check (idempotency): skip items that already have a PainPoint with
    extractor_model == llm.model_name unless force=True.

    Concatenate title + body for the LLM input, truncated to 4000 chars."""
```

`ExtractionReport(processed: int, painpoints_created: int, no_signal: int, failed: int, duration_ms: int)`.

Items are processed sequentially within a batch to keep the implementation simple. (Concurrency is a Plan B/C optimisation if needed — Ollama on a laptop is the bottleneck anyway.)

**Tests (using `MockLLMAdapter`):**
- `test_extract_creates_painpoint_for_high_signal` — seed a SourceItem with "wish there was a habit tracker for ADHD"; run; assert PainPoint exists with `extractor_model='mock-llm-v1'`.
- `test_extract_marks_no_signal_for_low_signal` — seed a plain news item; assert `extraction_state='no_signal'`, no PainPoint.
- `test_extract_skips_already_extracted_same_model` — pre-seed a PainPoint with the same `extractor_model`; run; assert no second PainPoint created.
- `test_extract_re_extracts_on_force` — same setup, `force=True` → second PainPoint inserted.
- `test_extract_skips_validation_role` — role='validation' SourceItem is not processed.
- `test_extract_marks_failed_on_exception` — patch `llm.extract_pain_point` to raise; assert `extraction_state='failed'`.

**Suggested commit:** `feat(pipeline): stage 1 — extract pain points from SourceItems`

---

### A-14 — Stage 2: embed.py

**Files:** `app/pipeline/embed.py`, `tests/pipeline/test_embed.py`

```python
async def run_embedding(
    session: AsyncSession,
    embedder: EmbeddingAdapter,
    *,
    batch_size: int = 64,
) -> EmbeddingReport:
    """Stage 2. Selects PainPoints where embedding IS NULL.
    Builds the embedded text as: f'{problem_text}. Audience: {audience}. {urgency_cue}'.
    Calls embedder.embed(batch) and writes the vectors back as JSON arrays.
    """
```

**Tests:**
- `test_embed_populates_null_embeddings` — seed 3 PainPoints with embedding=None; run; assert all 3 have non-empty embedding lists of length `embedder.dim`.
- `test_embed_skips_existing` — pre-seed one with embedding already set; assert it's untouched.
- `test_embed_handles_empty_batch` — no rows → no calls to `embedder.embed`.

**Suggested commit:** `feat(pipeline): stage 2 — embed pain-point texts`

---

### A-15 — Stage 3: identity_resolution.py

**Files:** `app/pipeline/identity_resolution.py`, `tests/pipeline/test_identity_resolution.py`

```python
async def run_identity_resolution(
    session: AsyncSession,
    *,
    threshold: float,
) -> IdentityResolutionReport:
    """Stage 3.

    For each PainPoint where candidate_id IS NULL and embedding IS NOT NULL:
        - Build EmbeddingIndex from all OpportunityCandidate rows (is_archived=False)
          using their .centroid as the vector.
        - For each unattached PainPoint, find nearest candidate.
        - If sim >= threshold: attach (PainPoint.candidate_id = matched.id),
          recompute that candidate's centroid as mean(painpoints.embedding),
          update last_evidence_at.
        - Else: leave candidate_id=NULL (will go to clustering).
    """
```

**Centroid recomputation:** mean of all attached pain-points' embeddings. Re-normalise to unit length so cosine remains stable. After updates, write `OpportunityCandidate.centroid = new_mean.tolist()` and `last_evidence_at = now`.

**Tests:**
- `test_identity_attaches_when_above_threshold` — pre-seed candidate with centroid=[1,0,0,...]; PainPoint with embedding=[0.95, 0.05, 0,...]; threshold=0.82; assert attached and centroid recomputed.
- `test_identity_leaves_unattached_below_threshold` — embedding orthogonal; assert candidate_id stays NULL.
- `test_identity_ignores_archived_candidates` — pre-seed archived candidate that would match; assert ignored.
- `test_identity_no_candidates_yet` — empty candidate table → all painpoints stay unattached, no error.
- `test_centroid_recomputation_correct` — after attaching, centroid = mean(unit vectors of all attached pps).

**Suggested commit:** `feat(pipeline): stage 3 — identity resolution against existing candidates`

---

### A-16 — Stage 4: clustering.py

**Files:** `app/pipeline/clustering.py`, `tests/pipeline/test_clustering.py`

Cluster the pain-points still unattached after stage 3.

**Algorithm:** start with **HDBSCAN** (`hdbscan` package). It doesn't require pre-specifying cluster count, handles noise (singletons stay unclustered, exactly the behaviour we want for `clustering_min_cluster_size`), and works well in 768d embedding spaces. If the implementer hits installation pain on Windows/WSL2 (HDBSCAN has a C extension), fall back to `sklearn.cluster.AgglomerativeClustering` with a distance threshold tuned to ~0.4 on cosine distance (= 0.6 cosine similarity, looser than the 0.82 identity-resolution threshold by design — clusters can be broader than within-candidate proximity).

```python
async def run_clustering(
    session: AsyncSession,
    *,
    min_cluster_size: int,
) -> ClusteringReport:
    """Stage 4.

    Loads all PainPoints where candidate_id IS NULL. Runs HDBSCAN on the
    embedding matrix with min_cluster_size. For each non-noise cluster:
        - Create a new OpportunityCandidate (problem_statement='[unlabelled]',
          centroid=cluster_mean, lifecycle_state=None, specificity=0).
        - Set every member PainPoint.candidate_id = new_candidate.id.
    Noise points (HDBSCAN label = -1) stay unattached — picked up next run.
    """
```

The fresh candidate is created in an "unlabelled" placeholder state; stage 5 fills in `problem_statement`, `audience`, `why_now`, `specificity`. Until then it has no centroid… wait, it does — the cluster mean. Just `problem_statement` is the placeholder.

**Tests (using `MockEmbeddingAdapter`'s 32-d vectors):**
- `test_clustering_groups_similar_points` — pre-seed 6 painpoints with 2 distinct semantic groups (use mock embeddings of `"habit tracker..."` × 3 and `"finance app..."` × 3 — they won't be perfectly similar via the hash mock, so use synthetic embeddings directly written into the DB to simulate the desired structure); assert 2 candidates created, 3 PainPoints each.
- `test_clustering_respects_min_cluster_size` — 2 painpoints with similar embeddings, min_cluster_size=3; assert 0 candidates created, both still unattached.
- `test_clustering_creates_unlabelled_candidates` — assert problem_statement is the placeholder string and specificity=0.

**Suggested commit:** `feat(pipeline): stage 4 — cluster unmatched pain points into new candidates`

---

### A-17 — Stage 5: labelling.py

**Files:** `app/pipeline/labelling.py`, `tests/pipeline/test_labelling.py`

For each `OpportunityCandidate` where `problem_statement == '[unlabelled]'` (or similar sentinel), call `llm.label_cluster(evidence_texts, category_slugs)`. Persist:

- `problem_statement`, `audience`, `why_now`, `specificity` from the response
- `labeller_model` = `llm.model_name`
- `category_id` = lookup by `suggested_category_slug` (or NULL if not found)
- Propagate `category_id` to all parent SourceItems via `UPDATE source_items SET category_id = :c WHERE id IN (SELECT source_item_id FROM pain_points WHERE candidate_id = :cid)`

```python
async def run_labelling(
    session: AsyncSession,
    llm: LLMAdapter,
) -> LabellingReport:
    """Stage 5. Labels all unlabelled candidates."""
```

Evidence text per candidate: top 10 PainPoints by recency (`extracted_at DESC`), formatted as `f"- {pp.problem_text} [{pp.audience}]"`.

**Tests (using `MockLLMAdapter`):**
- `test_labelling_populates_unlabelled_candidate` — pre-seed unlabelled candidate with 3 painpoints; run; assert problem_statement updated, specificity set.
- `test_labelling_skips_labelled` — pre-seed candidate with `problem_statement='already labelled'`; run; assert untouched.
- `test_labelling_assigns_category_when_known_slug` — mock returns `suggested_category_slug='wellness'`; pre-seed `wellness` Category; assert candidate.category_id matches.
- `test_labelling_null_category_when_unknown_slug` — mock returns `suggested_category_slug='spaceflight'` (not in DB); assert category_id=NULL.
- `test_labelling_propagates_category_to_source_items` — after labelling, parent SourceItem.category_id is set.

**Suggested commit:** `feat(pipeline): stage 5 — label clusters and assign category`

---

### A-18 — Pipeline orchestrator

**Files:** `app/pipeline/orchestrator.py`, `tests/pipeline/test_orchestrator.py`

```python
async def run_pipeline(
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMAdapter,
    embedder: EmbeddingAdapter,
    settings: Settings,
    *,
    since: datetime | None = None,
) -> PipelineReport:
    """Runs stages 1–5 in order. Each stage uses its own session/transaction.
    Returns a structured report (one row per stage)."""
```

Logging: emit one structured `pipeline_start` at the beginning and one `pipeline_complete` at the end with timing per stage. Per-stage logs are emitted by the stage implementations.

**Tests:**
- `test_orchestrator_runs_all_stages_in_order` — using mocks end-to-end; seed two SourceItems (high-signal, low-signal); assert one PainPoint created, one candidate created (alone, below min_cluster_size, so actually no candidate — good test of edge case), or with three high-signal items, one candidate created and labelled.
- `test_orchestrator_respects_since` — items with `ingested_at` before `since` are skipped.
- `test_orchestrator_idempotent` — running twice with no new data is a no-op (cache + null-checks ensure this).

**Suggested commit:** `feat(pipeline): orchestrator — sequential stage runner`

---

### A-19 — Scheduler integration

**Files:** `app/ingestion/scheduler.py`, `tests/test_scheduler.py`

Remove v3 jobs. The current scheduler likely has:
- 4 ingestion jobs (GitHub, HN, Reddit, mock AppStore — leave first 3, leave mock AppStore for Plan C)
- Daily scoring job (`_scoring_job`) — **remove**
- Daily brief job — **remove**
- Daily digest cron — **remove** (Plan B re-adds, repurposed)
- Spike alert (chained inside scoring) — **gone with scoring**
- Weekly pruning (M6) — **keep**
- Bulk-backfill on startup (M5.5) — **keep, but routed to v4 orchestrator after ingestion**

Add:

```python
scheduler.add_job(
    _pipeline_job,
    CronTrigger(hour=settings.pipeline_cron_hour, minute=0),  # default 03:30 UTC
    id="daily_pipeline",
    max_instances=1, coalesce=True, misfire_grace_time=3600,
)

async def _pipeline_job() -> None:
    async with session_factory() as session:
        await run_pipeline(session_factory, llm_adapter, embedding_adapter, settings)
```

`pipeline_cron_hour` is a new config (default 3) — runs at 03:30 UTC, before the (Plan B) digest at 08:00. The bot reference is *not* needed here — pipeline doesn't push.

**Tests:** add a `test_scheduler_registers_v4_jobs` — instantiate scheduler, assert job IDs `daily_pipeline`, `weekly_pruning`, `github_ingestion`, `hn_ingestion`, `reddit_ingestion` exist; assert `_scoring_job`, `_brief_job`, `digest_cron`, `spike_alert` are gone.

**Suggested commit:** `feat(scheduler): replace v3 daily jobs with v4 pipeline cron`

---

### A-20 — Bot handler trim

**Files:** `app/bot/handlers.py`, `app/bot/scheduler_hooks.py`, `tests/test_bot_handlers.py`

Strip v3 commands. Keep only:
- `/start` (existing)
- `/help` (existing — update text to say "v4 commands coming in next release")
- `/sources` (existing)

Remove handlers + registrations for `/briefing`, `/niches`, `/niche`, `/trending`. Drop `app/bot/scheduler_hooks.py`'s digest + spike push hooks; the scheduler no longer references them.

`set_my_commands` call in startup should be updated to the trimmed command list.

**Tests:** remove (or skip with `pytest.skip`) the existing v3 command tests — they'll become irrelevant. Plan B re-adds the v4 command tests. Adjust `test_bot_command_menu_registered` to assert only the trimmed list.

**Suggested commit:** `refactor(bot): strip v3 commands; keep /start /help /sources only`

---

### A-21 — Bulk-backfill v4 integration

**Files:** `app/ingestion/backfill.py`, `tests/test_backfill.py`

The existing M5.5 `bulk_backfill()` runs ingestion → `rebuild_historical_signals` → `score_all_niches_for_date` → `run_brief_for_niche`. Replace the post-ingestion phases with:

```python
async def bulk_backfill(...):
    ...
    # ingestion phase (unchanged)
    for connector in connectors:
        await connector.run(since=since_dt)

    # NEW: v4 pipeline runs once over the full backfilled corpus
    await run_pipeline(session_factory, llm, embedder, settings, since=since_dt)

    return BackfillReport(...)
```

`rebuild_historical_signals`, `score_all_niches_for_date`, `run_brief_for_niche` calls are deleted (along with their imports). The functions themselves still exist on disk until Plan C cleans them up.

**Tests:** existing `test_bulk_backfill` will need updating — replace the niche-signal/score assertions with: "after backfill, at least N PainPoints exist (corresponds to the high-signal fixture items)."

**Suggested commit:** `feat(backfill): route bulk_backfill through v4 pipeline`

---

### A-22 — `scripts/run_backfill.py` CLI

**Files:** `scripts/run_backfill.py`, `tests/test_run_backfill_cli.py`

```python
# Usage:
#   uv run python -m scripts.run_backfill --history-days 30 --llm-provider ollama
#   uv run python -m scripts.run_backfill --history-days 30 --llm-provider mock --db-url sqlite:///./test.db
```

Standalone CLI. Constructs adapters via `make_llm_adapter`/`make_embedding_adapter` honouring `--llm-provider`/`--embedding-provider` overrides (override the env). If `--db-url` is passed, use it instead of `settings.database_url`. Calls `bulk_backfill()` with the requested history depth. Always emits a structured `backfill_report` JSON line on completion.

**Tests:** invoke `main(["--history-days", "1", "--llm-provider", "mock", "--embedding-provider", "mock"])` against a fresh in-memory DB seeded with a couple of mock connectors; assert a `BackfillReport` is returned and at least one PainPoint exists.

**Suggested commit:** `feat(scripts): run_backfill CLI for provider-independent backfill`

---

### A-23 — End-to-end pipeline test

**Files:** `tests/pipeline/test_pipeline_e2e.py`

Single high-value integration test:

1. Spin up an in-memory SQLite via the existing test fixture.
2. Sync 6 categories.
3. Insert ~20 fixture SourceItems across roles:
   - 8 Reddit posts with explicit pain-point language ("wish there was an app that...")
   - 4 Reddit posts with no signal
   - 3 HN Show HN (validation, not extracted)
   - 3 HN Ask HN (extraction)
   - 2 GitHub repos (validation)
4. Build mock LLM + mock embedding adapters with deterministic clustering-friendly fixture vectors (write embeddings directly into PainPoints in a setup step to control cluster shapes — or use enough text variation that the hash-mock produces 2 distinct clusters).
5. Run `run_pipeline(...)`.
6. Assert:
   - 11 PainPoints created (8 Reddit hi-signal + 3 Ask HN)
   - SourceItems with role='validation' have `extraction_state='pending'` (not processed by stage 1)
   - SourceItems with role='ignored' (none in this fixture, but if added, also `pending`)
   - Validation-role items have no PainPoints
   - At least 2 OpportunityCandidates created with non-placeholder problem_statement
   - All candidates have specificity ≥ 1
   - Re-running the pipeline produces no new PainPoints (idempotency)
7. Run `run_pipeline()` a second time — assert PipelineReport has 0 painpoints created, 0 candidates created.

**Suggested commit:** `test(pipeline): end-to-end fixture-driven pipeline run`

---

### A-24 — Documentation: in-progress note

**Files:** `README.md`, `KANBAN.md`

`README.md`: add a banner at the top:

```markdown
> **v4 in progress (Plan A: Foundation & Pipeline Core).**
> Bot commands `/briefing`, `/niches`, `/niche`, `/trending` have been removed.
> v4 commands (`/opportunities`, `/opportunity`, `/categories`, `/emerging`)
> will be reintroduced in Plan B. See
> `docs/superpowers/specs/2026-04-28-opportunity-discovery-pivot-design.md`.
```

`KANBAN.md`: add a new section after Phase 1.5 backlog:

```markdown
## v4 — Opportunity Discovery (in progress)

| ID | Title | Plan |
|---|---|---|
| V4A-* | Foundation & Pipeline Core | docs/superpowers/plans/2026-04-28-v4a-foundation-and-pipeline-core.md |
| V4B-* | Scoring, Lifecycle, Bot UX, Feedback | docs/superpowers/plans/2026-04-28-v4b-scoring-lifecycle-bot-ux.md |
| V4C-* | Play Store Connector & v3 Decommissioning | docs/superpowers/plans/2026-04-28-v4c-playstore-and-decommissioning.md |
```

(Detailed task tracking lives in the plan files; KANBAN just points to them.)

**Suggested commit:** `docs(v4): mark Plan A in progress in README and KANBAN`

---

## Definition of Done — Plan A

- [ ] `migrate_to_v4.py` runs cleanly and the new schema is in place
- [ ] `data/categories.yaml` is synced to the `categories` table on startup
- [ ] All four connectors role-tag their items correctly; HN split is enforced
- [ ] Stage 1–5 each have unit tests passing with mock adapters
- [ ] `run_backfill.py --llm-provider mock` completes against a fresh DB and creates at least one OpportunityCandidate from fixture data
- [ ] Daily pipeline cron registers and runs cleanly (verified by triggering manually with `MockLLMAdapter`)
- [ ] Bot still starts; `/start`, `/help`, `/sources` respond; v3 commands removed
- [ ] Full test suite green: `uv run pytest`
- [ ] No references to `Niche`, `NicheSignal`, `NicheScoreHistory`, `OpportunityBrief` remain in active code paths (legacy modules in `app/agents/`, `app/forecasting/scoring.py` are *unimported* but not yet deleted — Plan C cleans up)

---

## Risks & Mitigations (Plan A specific)

| Risk | Mitigation |
|---|---|
| `hdbscan` fails to install on WSL2 | Fallback `AgglomerativeClustering` documented; switch by changing one import in `clustering.py`. |
| Ollama embeddings endpoint not batched in user's version | Iterate per-text inside `OllamaEmbeddingAdapter.embed`; performance hit is acceptable for Plan A scale. |
| LLM JSON output drift on `qwen2.5` | `format='json'` + Pydantic validation + `has_unmet_need=False` fallback for malformed extraction. Labelling failures raise (loud) — acceptable since labelling is rarer. |
| `MockLLMAdapter` heuristic too narrow / wide for tests | Tests can build PainPoints directly when needed (skip stage 1) to test stages 2+ in isolation. |
| Bot becomes useless during Plan A | Acknowledged: `/start /help /sources` only. README banner explains. |

---

*End of Plan A.*
