# Plan — v4.C: Play Store Connector, NIM Adapter, Weekly Re-cluster, v3 Decommissioning

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.
>
> **Environment note:** Same Windows/WSL2 + `uv` constraints as Plans A & B. Pass commands to the user; no direct git commits.

**Goal:** Bring v4 to feature-complete by (a) replacing the App Store mock with a real Play Store reviews connector, (b) implementing the NVIDIA NIM adapter for cloud deployment, (c) wiring the weekly re-cluster housekeeping pass, and (d) physically removing all v3 code/data/docs. End state: v4 is the only system; v3 has no on-disk presence except in `docs/archive/v3/`.

**Architecture:** Plug `google-play-scraper` and an optional iOS RSS connector into the existing `BaseConnector` interface — they extract pain-points just like Reddit. NIM adapters drop into the factory's `nim` branch alongside the Ollama implementations. The weekly re-cluster pass is a new APScheduler cron (Sundays 04:00 UTC) that re-clusters the rolling window and merges/splits candidates using the spec's `merged_into_id` bookkeeping. Decommissioning is a sweep of file deletions + ADR writes + doc archival.

**Tech Stack:** Adds `google-play-scraper` (Python lib, no auth). Adds `httpx` calls to NIM endpoints (httpx already a dependency). No other new packages.

**Spec reference:** `docs/superpowers/specs/2026-04-28-opportunity-discovery-pivot-design.md`

**Depends on:** Plans A & B both complete and merged. The system is already producing candidates and surfacing them via Telegram; Plan C is the last mile.

---

## Context

After Plan B the v4 system is functional but incomplete in three ways:
1. **Play Store mock is a placeholder.** The user explicitly called Play Store reviews "a goldmine" — until they're real, the system is missing its highest-signal source.
2. **NIM adapter is unimplemented.** Plan A's factory raises `NotImplementedError` for `LLM_PROVIDER=nim`; Plan C makes cloud deployment possible.
3. **v3 code still occupies disk.** `app/agents/`, `app/forecasting/scoring.py`, `app/ingestion/appstore_mock_connector.py`, `data/niches.yaml`, `data/mock/` — all unused but present. Removing them tightens the codebase and prevents accidental imports.

Two design points from spec §4.3 also land in Plan C:
- **Weekly re-cluster pass** — re-clusters the rolling 30-day window and merges drifted candidates / splits over-broad ones. This is the long-term answer to identity-resolution drift.
- **`merged_into_id` bookkeeping helper** — `resolve_candidate_root(id)` for traversing merge chains.

---

## File-Level Plan

**New files:**
- `app/ingestion/playstore_connector.py` — Real connector using `google-play-scraper`
- `app/ingestion/ios_rss_connector.py` — Optional, behind feature flag
- `app/ingestion/playstore_app_discovery.py` — Weekly job: top-N apps per Play Store category
- `app/llm/nim_adapter.py` — NIM `LLMAdapter` implementation
- `app/llm/nim_embedding_adapter.py` — NIM `EmbeddingAdapter`
- `app/pipeline/recluster.py` — Weekly re-cluster pass
- `app/db_helpers/candidate_resolution.py` — `resolve_candidate_root()` helper
- Tests under `tests/ingestion/test_playstore_connector.py`, `tests/llm/test_nim_adapters.py`, `tests/pipeline/test_recluster.py`

**Modified:**
- `app/llm/factory.py` — replace `NotImplementedError` for NIM
- `app/ingestion/scheduler.py` — add `playstore_ingestion`, `playstore_app_discovery`, `weekly_recluster` cron jobs
- `app/config.py` + `.env.example` — add Play Store and NIM keys (most already declared in Plan A)
- `pyproject.toml` — add `google-play-scraper` dependency
- `docs/decisions.md` — append ADR-009, ADR-010, ADR-011
- `devtrend-project-document.md` — bump to v4
- `docs/roadmap.md` — update Phase 1.5 / Phase 2 sections
- `docs/evaluation-plan.md` — small addendum (v4 review checklists already added in Plan B)
- `README.md` — update for Play Store + NIM

**Removed (file deletions):**
- `app/agents/` — entire directory
- `app/forecasting/scoring.py`
- `app/forecasting/__init__.py` (becomes empty after the file above is gone — delete the directory entirely)
- `app/ingestion/appstore_mock_connector.py`
- `data/mock/` — entire directory and JSON files
- `data/niches.yaml`
- `tests/test_agents.py` (or whatever the M4 LangGraph tests are called — verify before deletion)
- `tests/test_forecasting.py` (if exists)
- Any v3-only fixture files under `tests/fixtures/` that target niches
- v3 sections of `devtrend-project-document.md` are *moved* (not deleted) to `docs/archive/v3/devtrend-project-document-v3.md`

---

## Tasks

### C-01 — `google-play-scraper` dependency

**Files:** `pyproject.toml`, lock file regen

```toml
[project]
dependencies = [
    # ... existing
    "google-play-scraper>=1.2,<2.0",
]
```

Have user run `uv sync` and verify the import works in Python: `from google_play_scraper import app, reviews, Sort`.

**No tests** — pure dependency add.

**Suggested commit:** `chore(deps): add google-play-scraper`

---

### C-02 — Play Store app discovery

**Files:** `app/ingestion/playstore_app_discovery.py`, `data/playstore_seed_apps.yaml` (initial seed; weekly job overwrites), `tests/ingestion/test_playstore_app_discovery.py`

`google-play-scraper` exposes `list()` for top-N apps per category. Map our 6 internal categories to Play Store category IDs:

```python
CATEGORY_MAP = {
    "wellness":     ["HEALTH_AND_FITNESS", "MEDICAL"],
    "finance":      ["FINANCE"],
    "devtools":     ["TOOLS", "PRODUCTIVITY"],
    "productivity": ["PRODUCTIVITY", "BUSINESS"],
    "creative":     ["ART_AND_DESIGN", "PHOTOGRAPHY"],
    "gaming":       ["GAME"],   # high level — Play Store has dozens of GAME_* sub-cats
}
```

```python
async def refresh_app_list(
    settings: Settings,
    *,
    top_n_per_category: int = 50,
) -> list[AppListing]:
    """Calls google_play_scraper.list() for each (internal_cat, ps_cat) pair.
    Returns deduplicated list of (app_id, title, category_internal_slug)."""
```

`google-play-scraper` is sync — wrap calls in `asyncio.to_thread` to keep the event loop happy (ADR-002 compliance).

The discovered list is persisted to a small new table `TrackedApp(app_id PK, title, internal_category, last_seen_at)`. Weekly job upserts; never deletes (an app dropping out of the top-50 doesn't mean we should stop watching its reviews).

Add `TrackedApp` to `app/models.py`.

**Tests (using a recorded fixture from `google_play_scraper.list()`):**
- `test_refresh_app_list_returns_top_n` — patch `list()` to return 50 apps × 6 categories; assert correct count.
- `test_refresh_app_list_dedupes` — same `app_id` returned by two categories; assert one row.
- `test_refresh_app_list_upserts_existing` — pre-seed an app; assert `last_seen_at` updated.

**Suggested commit:** `feat(ingestion): Play Store app discovery + persistence`

---

### C-03 — Play Store reviews connector

**Files:** `app/ingestion/playstore_connector.py`, `tests/ingestion/test_playstore_connector.py`

Subclass `BaseConnector`. `fetch()` iterates over `TrackedApp` rows and calls `google_play_scraper.reviews()` for each:

```python
class PlayStoreReviewsConnector(BaseConnector):
    source_type = "playstore"

    async def fetch(self, *, since: datetime | None = None) -> list[RawItem]:
        apps = await self._load_apps(limit=settings.playstore_top_n_per_category * 6)
        items: list[RawItem] = []
        for app in apps:
            try:
                raw, _ = await asyncio.to_thread(
                    reviews,
                    app.app_id,
                    sort=Sort.NEWEST,
                    count=settings.playstore_reviews_per_app,
                    lang="en", country="us",
                )
            except Exception as e:
                log.warning("playstore_fetch_failed", app_id=app.app_id, error=str(e))
                continue
            for r in raw:
                if since and r["at"] < since:
                    continue
                items.append(_to_raw_item(app, r))
        return items

    def normalize(self, raw: RawItem) -> SourceItem:
        return SourceItem(
            source_type="playstore",
            external_id=raw["review_id"],
            title=raw["title"] or "",          # Play Store reviews often have no title
            body=raw["content"],
            url=f"https://play.google.com/store/apps/details?id={raw['app_id']}",
            created_at=raw["at"],
            metadata_json={"app_id": raw["app_id"], "rating": raw["score"], "lang": "en"},
            role="extraction",
            extraction_state="pending",
        )
```

**Rate-limit handling:** `google-play-scraper` doesn't expose an HTTP client we can wrap with `_request_with_retry`. The pragmatic approach: add a small async semaphore (`asyncio.Semaphore(5)`) bounding parallel `to_thread` calls, plus a 1-second sleep between apps. If Play Store starts returning empty results, log `playstore_likely_throttled` and abort the run gracefully.

**Tests:**
- `test_playstore_normalize_produces_extraction_role`
- `test_playstore_fetch_dedupes_by_external_id` — fixture with same review id appearing in two responses; assert one SourceItem.
- `test_playstore_respects_since` — review with `at` before since; assert filtered out.
- `test_playstore_continues_on_app_failure` — one app raises; assert other apps still processed.

**Suggested commit:** `feat(ingestion): real Play Store reviews connector`

---

### C-04 — iOS App Store RSS connector (optional, behind flag)

**Files:** `app/ingestion/ios_rss_connector.py`, `app/config.py`, `tests/ingestion/test_ios_rss_connector.py`

Apple's RSS feeds (e.g. `https://itunes.apple.com/us/rss/customerreviews/id={app_id}/sortBy=mostRecent/page=1/json`) return up to 500 most-recent reviews per app, free, no auth. Implementer should confirm endpoint shape before coding — Apple has reshuffled this URL pattern multiple times.

```python
class IosRssReviewsConnector(BaseConnector):
    source_type = "ios_appstore"
    # disabled unless settings.enable_ios_rss == True
```

Reuse the `TrackedApp` table to track which apps to fetch — same app may appear on both stores. Add an `ios_app_id` nullable column to `TrackedApp` so a row can carry both the Play Store `app_id` and the iOS numeric id when the same app exists on both stores.

If `settings.enable_ios_rss=False` (default), this connector is not registered with the scheduler.

**Tests:**
- Fixture-driven happy path
- `test_ios_rss_disabled_by_flag` — connector class isn't registered when flag is off

**Suggested commit:** `feat(ingestion): optional iOS App Store RSS connector behind enable_ios_rss flag`

---

### C-05 — NIM LLM adapter

**Files:** `app/llm/nim_adapter.py`, `tests/llm/test_nim_adapter.py`

NVIDIA NIM is an OpenAI-compatible chat-completions API. The adapter is structurally similar to `OllamaAdapter` but hits NIM's endpoint and includes the API key.

```python
class NvidiaNimAdapter(LLMAdapter):
    def __init__(self, api_key: str, model: str = "meta/llama-3.1-70b-instruct",
                 base_url: str = "https://integrate.api.nvidia.com/v1") -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self._model = model

    @property
    def model_name(self) -> str:
        return f"nim:{self._model}"     # cache key namespaces NIM models distinctly from Ollama

    async def extract_pain_point(self, source_item_text, *, model_hint=None):
        messages = [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": EXTRACT_USER_PROMPT.format(text=source_item_text[:4000])},
        ]
        response = await self._client.post(
            "/chat/completions",
            json={"model": model_hint or self._model, "messages": messages,
                  "response_format": {"type": "json_object"}, "temperature": 0.0},
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        try:
            return PainPointDraft.model_validate_json(raw)
        except (ValidationError, json.JSONDecodeError) as e:
            log.warning("nim_extract_invalid_json", error=str(e), raw=raw[:200])
            return PainPointDraft(has_unmet_need=False)
```

`label_cluster` and `generate_brief` follow the same shape as `OllamaAdapter`'s — same prompts (defined once in `app/llm/prompts.py`, shared between adapters; this also justifies relocating prompts out of `app/agents/` before C-08 deletes that directory).

**Important: relocate prompt templates first.** The current `app/agents/prompts.py` contains the v4 prompts (added in Plan A task A-08). Move them to `app/llm/prompts.py` so deleting `app/agents/` in C-08 doesn't break Ollama+NIM. This relocation is a sub-step of C-05.

**Tests:**
- `test_nim_adapter_calls_correct_endpoint` — assert POST to `/chat/completions` with correct headers.
- `test_nim_adapter_handles_5xx_with_retry` — first call returns 503, second 200; assert success.
- `test_nim_adapter_invalid_json_falls_back_to_no_signal`
- `test_nim_adapter_model_name_namespaced` — assert `model_name == 'nim:meta/llama-3.1-70b-instruct'`.

**Suggested commit:** `feat(llm): NVIDIA NIM adapter for cloud deployment`

---

### C-06 — NIM embedding adapter

**Files:** `app/llm/nim_embedding_adapter.py`, `tests/llm/test_nim_adapters.py`

```python
class NvidiaNimEmbeddingAdapter(EmbeddingAdapter):
    def __init__(self, api_key: str, model: str = "nvidia/nv-embedqa-e5-v5",
                 base_url: str = "https://integrate.api.nvidia.com/v1") -> None: ...

    @property
    def dim(self) -> int:
        return 1024  # nv-embedqa-e5-v5

    @property
    def model_name(self) -> str:
        return f"nim:{self._model}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.post(
            "/embeddings",
            json={"model": self._model, "input": texts, "input_type": "query"},
        )
        response.raise_for_status()
        return [item["embedding"] for item in response.json()["data"]]
```

The dim mismatch between Ollama (`nomic-embed-text`, 768) and NIM (`nv-embedqa-e5-v5`, 1024) means you cannot mix backfills across providers without re-embedding. This is acceptable: backfill via local Ollama, then daily-incremental via NIM means the Ollama-embedded population stays put and only new PainPoints get NIM-embedded. **But:** new NIM PainPoints have a different dim, so identity resolution against Ollama centroids breaks.

**Resolution:** track `embedding_dim` and `embedding_model` per `OpportunityCandidate`. Only match a new pain-point against candidates with matching dim. Cross-dim matching is impossible; new clusters form among NIM pain-points and stay separate from Ollama clusters until a one-off "re-embed everything" script normalises the population.

**Add to `OpportunityCandidate`:** `embedding_model: Mapped[str]` (e.g. `'nim:nvidia/nv-embedqa-e5-v5'`). Same column on `PainPoint`. Update identity-resolution stage (Plan A's `app/pipeline/identity_resolution.py`) to filter candidates by matching `embedding_model`.

**Schema migration:** add columns via raw SQL `ALTER TABLE` in a new `scripts/migrate_to_v4_2.py` (small follow-up migration). Or — simpler — have implementer drop+recreate locally since deployment is still local-only at this point.

**Tests:**
- `test_nim_embedding_adapter_dim` — assert `dim == 1024`.
- `test_identity_resolution_filters_by_embedding_model` — pre-seed 2 candidates with different `embedding_model`; new pain-point with one of the models; assert match only against the same-model candidate.

**Suggested commit:** `feat(llm): NIM embedding adapter + per-candidate embedding_model tracking`

---

### C-07 — Update factory for NIM

**Files:** `app/llm/factory.py`, `tests/llm/test_factory.py`

Replace the `NotImplementedError` branches:

```python
def make_llm_adapter(settings):
    match settings.llm_provider:
        case "ollama": return OllamaAdapter(...)
        case "nim":    return NvidiaNimAdapter(api_key=settings.nim_api_key, model=settings.nim_llm_model)
        case "mock":   return MockLLMAdapter()
        ...
```

Validation: if `LLM_PROVIDER=nim` and `NIM_API_KEY=""`, raise `ValueError` at startup with a clear message ("NIM_API_KEY required when LLM_PROVIDER=nim").

**Tests:**
- `test_factory_returns_nim_adapter_when_configured`
- `test_factory_raises_when_nim_key_missing`

**Suggested commit:** `feat(llm): factory selects NIM adapters based on env`

---

### C-08 — Weekly re-cluster pass

**Files:** `app/pipeline/recluster.py`, `app/db_helpers/candidate_resolution.py`, `tests/pipeline/test_recluster.py`

```python
async def run_weekly_recluster(
    session: AsyncSession,
    embedder: EmbeddingAdapter,
    *,
    window_days: int = 30,
    merge_threshold: float = 0.88,
    split_silhouette_threshold: float = 0.3,
) -> ReclusterReport:
    """Spec §4.3 + identity-resolution drift mitigation.

    1. Load all PainPoints in the last window_days WITH the matching embedding_model.
    2. Re-cluster from scratch using HDBSCAN (same algorithm as Stage 4).
    3. Compare new clusters to existing OpportunityCandidate centroids:
        - For each new cluster, find best-matching active candidate by cosine sim
          on cluster mean vs centroid.
        - If sim >= merge_threshold AND multiple existing candidates match the same
          new cluster: archive all but the highest-scoring; set merged_into_id
          on the others; reattach orphaned PainPoints to the survivor.
        - If a single existing candidate's PainPoints split across multiple new
          clusters with low intra-cluster cohesion (silhouette < split_silhouette_threshold):
          split — keep the largest sub-cluster as the original candidate;
          spawn new candidates for the others; reattach PainPoints accordingly.
    4. After merges/splits: recompute centroids, last_evidence_at, created_at
       (= min across merged set), and re-trigger labelling for any candidate
       whose evidence set changed materially (e.g. >30% turnover).
    5. Return ReclusterReport(merged_count, split_count, relabelled_count, ...).
    """
```

`resolve_candidate_root(candidate_id)` helper traverses `merged_into_id` chains:

```python
async def resolve_candidate_root(session, candidate_id: int) -> int:
    """Returns the surviving (non-archived) candidate id at the end of a merge chain.
    Raises if a cycle is detected (shouldn't happen but defensive)."""
    seen = set()
    cur = candidate_id
    while True:
        if cur in seen: raise RuntimeError(f"merge cycle at {cur}")
        seen.add(cur)
        c = await session.get(OpportunityCandidate, cur)
        if c is None or not c.is_archived or c.merged_into_id is None:
            return cur
        cur = c.merged_into_id
```

**Re-trigger labelling** when evidence-set turnover is significant: if more than 30% of attached PainPoints are different from those at last labelling, mark `problem_statement = '[unlabelled]'` and let the next pipeline run's stage 5 re-label.

**Cron registration:** Sundays 04:00 UTC, after pruning at 03:00 (ADR-008) and the daily pipeline ran the day before.

**Tests:**
- `test_recluster_merges_drifted_candidates` — pre-seed 2 candidates whose pain-points have drifted toward each other; mock embeddings; assert one candidate becomes archived with `merged_into_id` set.
- `test_recluster_splits_overbroad_candidate` — pre-seed 1 candidate with 6 painpoints in 2 sub-clusters; assert one new candidate spawned, original retains majority sub-cluster.
- `test_resolve_candidate_root_walks_chain`
- `test_resolve_candidate_root_detects_cycle`
- `test_recluster_does_nothing_when_clusters_stable` — re-running on coherent data: 0 merges, 0 splits.
- `test_recluster_filters_by_embedding_model` — pain-points with two different embedding_models stay in separate populations.

**Suggested commit:** `feat(pipeline): weekly re-cluster + merge/split bookkeeping`

---

### C-09 — Move v4 prompts out of `app/agents/`

**Files:** `app/llm/prompts.py` (new), `app/agents/prompts.py` (delete in C-10), all callers

Plan A's task A-08 added v4 extract/label prompts to `app/agents/prompts.py` (pragmatic — same file as v3 LangGraph prompts). With C-10 about to delete `app/agents/`, relocate the v4 prompts to `app/llm/prompts.py`. Update imports in `OllamaAdapter` and the new `NvidiaNimAdapter` to point at the new module.

**Tests:** existing prompt tests should still pass after import path update.

**Suggested commit:** `refactor(llm): move v4 prompts to app/llm/prompts.py`

---

### C-10 — Delete v3 code

**Files:** various — see list

Sequential deletions. After each chunk, run the full test suite (`uv run pytest`) and confirm green before proceeding.

**Chunk 1: Agents directory.**
- `rm -rf app/agents/`
- Verify no imports of `app.agents.*` remain (`grep -r "from app.agents"` and `grep -r "import app.agents"` in `app/` and `tests/`). Update any stragglers.

**Chunk 2: Forecasting module.**
- `rm app/forecasting/scoring.py`
- If `app/forecasting/` is now empty, remove the directory and its `__init__.py`.
- Search for imports.

**Chunk 3: App Store mock.**
- `rm app/ingestion/appstore_mock_connector.py`
- `rm -rf data/mock/`
- Remove the connector from `app/ingestion/scheduler.py`'s registration list.

**Chunk 4: Niches YAML.**
- `rm data/niches.yaml`
- Search for any lingering reference (the `sync_niches_from_yaml` function may have been kept around in Plan A; confirm it's gone or remove now).

**Chunk 5: Obsolete tests.**
- Identify v3-only tests: anything that exclusively imports `Niche`, `NicheSignal`, `NicheScoreHistory`, `OpportunityBrief`, `app.agents.*`, `app.forecasting.scoring`. Delete those files.
- Tests that mix v3 + v4 fixtures (e.g. `test_models.py`) — keep, but trim the v3 fixture sections.

After all chunks: full test suite green; no broken imports; coverage report shows no untouched v3 modules.

**Suggested commit:** `chore(v3): remove obsolete agents, forecasting, mock connector, niches yaml`

---

### C-11 — ADR-009: Pivot to opportunity discovery

**Files:** `docs/decisions.md`

Append a new ADR entry below ADR-008. Context section can lift directly from spec §1 (Motivation). Decision section summarises the one-line product reframe and references the spec for full details. Consequences section enumerates the surfaces touched (schema, scheduler, bot UX, scoring) and that v3 is removed in C-10.

ADR length should match existing entries (~150–200 words). Don't repeat the spec — link to it.

**Suggested commit:** `docs(adr): ADR-009 — pivot to opportunity discovery`

---

### C-12 — ADR-010: Retire LangGraph

**Files:** `docs/decisions.md`

Reverses ADR-005. Context: ADR-005 was right for v3's per-niche brief generation, but v4's pipeline operates on collections (extract over batches of items, cluster over today's unmatched, score across the population). Each stage is a coherent unit you run, test, and replay independently — LangGraph's value (orchestrating per-record agent steps) doesn't apply.

Decision: remove `app/agents/`. Stages are plain async functions composed in `app/pipeline/orchestrator.py`. Adapter DI pattern from ADR-005 §5 is retained — the only change is dropping the LangGraph runtime.

Consequences: simpler control flow, easier debugging, easier replay. Plan B's lifecycle/scoring/bot work depends on this simplification (chained scoring → alert is one async coroutine, not a graph step).

**Suggested commit:** `docs(adr): ADR-010 — retire LangGraph in favour of explicit pipeline stages`

---

### C-13 — ADR-011: Identity resolution & weekly re-clustering

**Files:** `docs/decisions.md`

Document the design from spec §4.3 + Plan A task A-15 + Plan C task C-08:

- **Trigger condition:** identity resolution runs every daily pipeline run (Stage 3); weekly re-cluster runs Sundays 04:00 UTC.
- **Threshold values:** `IDENTITY_RESOLUTION_THRESHOLD=0.82` for daily attachment, `merge_threshold=0.88` for weekly re-cluster (higher because merging archives a candidate, which is more destructive than attaching a pain-point).
- **Merge strategy:** archive lower-scoring candidate, set `merged_into_id`, reattach all PainPoints, recompute centroid, preserve oldest `created_at`.
- **Split strategy:** keep majority sub-cluster on original candidate, spawn new candidates for other sub-clusters, mark each for re-labelling.
- **Embedding-model filtering:** identity resolution and re-clustering both filter by `embedding_model` to avoid cross-provider dim mismatches.

Consequences: candidates have stable identity across weeks even as evidence shifts; re-cluster is the long-term defence against drift.

**Suggested commit:** `docs(adr): ADR-011 — identity resolution + weekly re-cluster`

---

### C-14 — Bump project document to v4

**Files:** `devtrend-project-document.md`, `docs/archive/v3/devtrend-project-document-v3.md`

1. Copy current `devtrend-project-document.md` → `docs/archive/v3/devtrend-project-document-v3.md` (verbatim — historical snapshot).
2. Rewrite the live document for v4. The structure can mirror v3 but content reflects spec §1–§5:
   - **Overview** — one-paragraph product framing with the "opportunity discovery engine" framing
   - **Goals** — pull from spec §1 + §3
   - **Non-Goals** — Phase 2 items still deferred
   - **Decisions** — table of v4 decisions (this is essentially spec §2)
   - **High-Level Architecture** — pipeline diagram (spec §3.1)
   - **Repository Structure** — current state after C-10 deletions
   - **Telegram Bot Design** — v4 commands + push flows + feedback (spec §7)
   - **Data Sources** — Reddit, HN (split), GitHub (validation), Play Store (real)
   - **Canonical Data Model** — spec §4
   - **Scoring Design** — spec §5
   - **Pipeline Design** — replaces the v3 agent section; spec §3
   - **Scheduling Strategy** — daily pipeline, scoring, digest, weekly recluster, weekly playstore discovery, weekly pruning
   - **Configuration** — full v4 .env list
   - **Logging and Traceability** — same patterns as v3
   - **Evaluation Strategy** — link to evaluation-plan.md
   - **Risk Register** — spec §12
   - **Implementation Roadmap** — point at the three plan files
   - **Definition of Done — v4** — same checklist used at the end of Plan C below

Length target: similar to v3 (~700–800 lines). Lots of content can be lifted from the spec; the project doc is the user-facing version.

**Suggested commit:** `docs(v4): bump project document to v4 + archive v3`

---

### C-15 — Update roadmap

**Files:** `docs/roadmap.md`

Update each section per spec §11:

- **Phase 1 — Core MVP** (v1–v3) — mark as superseded; Phase 1 of the original roadmap is closed; v4 has replaced it.
- **Phase 1.5 — Signal Expansion** — Stack Overflow connector still pending; Google Trends still pending; Prophet now superseded by Momentum (drop the bullet); Competition dimension replaced by Validation (note this).
- **Phase 2 — Scale and Intelligence** — pgvector pulled forward conceptually but not implemented; web dashboard still pending; multi-user Telegram still pending; ARIMA / deep forecasting still pending.

Add a new top section **v4 — Opportunity Discovery (current):**

```markdown
## v4 — Opportunity Discovery

**Goal:** Reframe DevTrend from "trending niche tracker" to "opportunity discovery engine."
Mid-precision app hypotheses, persistent candidates, GitHub-as-validator,
Play Store reviews as gold extraction source.

| Plan | Focus | Status |
|---|---|---|
| v4.A | Foundation & Pipeline Core | <!-- update on completion --> |
| v4.B | Scoring, Lifecycle, Bot UX, Feedback | |
| v4.C | Play Store, NIM, Re-cluster, v3 Decommissioning | |

See `docs/superpowers/specs/2026-04-28-opportunity-discovery-pivot-design.md` for full design.
```

**Suggested commit:** `docs(roadmap): bump for v4`

---

### C-16 — Update README

**Files:** `README.md`

Rewrite the README around v4. Sections:

1. **What it is** (1 paragraph): opportunity discovery engine, Telegram-first, local-or-cloud.
2. **Quick start** (dev): `uv sync` → `uv run python -m scripts.migrate_to_v4 --confirm` → `uv run python -m scripts.run_backfill --history-days 30 --llm-provider ollama` → `uv run uvicorn app.main:app --reload`. Mention Ollama prereqs (running locally with `qwen2.5` and `nomic-embed-text` pulled).
3. **Bot commands** (table): `/start`, `/help`, `/opportunities`, `/opportunity <id>`, `/categories`, `/category <slug>`, `/emerging`, `/sources`.
4. **Architecture** (1 diagram + paragraph): pipeline 8 stages + daily/weekly cadence.
5. **Configuration**: `.env` keys, mention provider switching for cloud.
6. **Sources**: bullet list of where data comes from + Reddit UA reminder.
7. **Backfill workflow**: explain the local-Ollama-then-NIM operational pattern (spec §8).
8. **Testing**: `uv run pytest`.
9. **Limitations**: Reddit 1000-post ceiling, Play Store scraper TOS risk, scoring weights are calibration TODO.

**Suggested commit:** `docs(readme): rewrite for v4`

---

### C-17 — Scheduler additions

**Files:** `app/ingestion/scheduler.py`, `tests/test_scheduler.py`

Add three new jobs:

```python
# Play Store ingestion (daily, after pipeline + scoring)
scheduler.add_job(
    _playstore_ingestion_job,
    CronTrigger(hour=settings.playstore_cron_hour),  # default 02:00 UTC
    id="playstore_ingestion",
    max_instances=1, coalesce=True, misfire_grace_time=3600,
)

# Play Store app-list refresh (weekly Mon 02:30 UTC)
scheduler.add_job(
    _playstore_app_discovery_job,
    CronTrigger(day_of_week="mon", hour=2, minute=30),
    id="playstore_app_discovery",
    max_instances=1, coalesce=True,
)

# Weekly re-cluster (Sun 04:00 UTC, after pruning at 03:00)
scheduler.add_job(
    _weekly_recluster_job,
    CronTrigger(day_of_week=settings.weekly_recluster_cron_day,
                hour=settings.weekly_recluster_cron_hour),
    id="weekly_recluster",
    max_instances=1, coalesce=True,
)
```

iOS RSS ingestion is registered conditionally on `settings.enable_ios_rss`.

**Tests:** `test_scheduler_v4c_jobs_registered` — assert all three new IDs present.

**Suggested commit:** `feat(scheduler): Play Store + weekly re-cluster crons`

---

### C-18 — End-to-end fresh-deploy walkthrough

**Files:** `tests/integration/test_fresh_deploy.py` (new directory)

Single big-picture integration test exercising the entire fresh-install workflow with mocks:

1. Start with empty SQLite (in-memory).
2. Run migration script (`migrate_to_v4 --confirm`).
3. Run `bulk_backfill` with mock connectors providing 30 days of fixture data, mock LLM, mock embeddings.
4. Assert: candidates exist, scoring history exists for the latest day, briefs exist for top-3.
5. Trigger `_digest_job` with mock bot; assert it pushes a message containing top-3.
6. Fire a `fb:up:1` callback; assert `CandidateFeedback` row created.
7. Trigger `_weekly_recluster_job`; assert no merges/splits on coherent data.
8. Run pipeline a second day with new fixture data; assert new PainPoints attach to existing candidates (identity resolution working) AND new clusters form for genuinely-new evidence.
9. Trigger `_playstore_ingestion_job` with a mocked `google_play_scraper.reviews()`; assert 5 SourceItems with role='extraction', source_type='playstore'.

This test is slow (it runs a lot of real code paths). Mark with `@pytest.mark.integration` and exclude from default `pytest` run; the user runs it explicitly when they want a smoke check.

**Suggested commit:** `test(integration): end-to-end fresh-deploy walkthrough`

---

### C-19 — Extend pruning job for v4 entities

**Files:** `app/maintenance/pruning.py`, `tests/test_pruning.py`

The M6 pruning job (`prune_old_data`) deletes `SourceItem` > 90d and `NicheSignal` > 30d. v4 introduces two new prunable tables:

- `CandidateValidation` — keep only the most recent snapshot per candidate; delete older rows beyond 30 days.
- `LifecycleEvent` (added in Plan B B-05) — delete rows older than 30 days.

`PainPoint` does *not* need explicit pruning here — Plan A's `ondelete='CASCADE'` on `PainPoint.source_item_id` already deletes orphans automatically when `SourceItem` rows are pruned.

`OpportunityCandidate`, `CandidateScoreHistory`, `CandidateBrief`, `CandidateFeedback` are kept forever (spec §4.4 — load-bearing for momentum windows and longitudinal narratives).

Update `prune_old_data()`:

```python
async def prune_old_data(now: datetime) -> PruneReport:
    # ... existing SourceItem + NicheSignal deletes ...

    # NEW: keep latest CandidateValidation per candidate; drop older > 30d
    cv_cutoff = now - timedelta(days=30)
    await session.execute(text("""
        DELETE FROM candidate_validations
         WHERE validated_at < :cutoff
           AND id NOT IN (
               SELECT MAX(id) FROM candidate_validations GROUP BY candidate_id
           )
    """), {"cutoff": cv_cutoff})

    # NEW: drop LifecycleEvent > 30d
    await session.execute(
        delete(LifecycleEvent).where(LifecycleEvent.recorded_at < cv_cutoff)
    )
```

`PruneReport` gets two new fields: `candidate_validations_deleted`, `lifecycle_events_deleted`.

The `NicheSignal` clause is now dead (the table is gone after Plan A); delete it from the function. Same for the keep-list of metric names.

**Tests:**
- `test_prune_keeps_latest_validation_per_candidate` — pre-seed 3 CandidateValidation rows for one candidate at days -45/-20/-5 from now; assert -45 is deleted, -20 and -5 are kept (because the most-recent-per-candidate query keeps the newest, and -20 is within 30d).
- `test_prune_deletes_old_lifecycle_events` — seed events at -10d and -45d; assert -45d deleted.
- `test_prune_painpoints_cascade_with_source_items` — seed a SourceItem with a child PainPoint; SourceItem.created_at = -100d; run prune; assert both deleted.

**Suggested commit:** `feat(maintenance): extend pruning job for v4 entities`

---

### C-20 — Final lint + type check

**Files:** the whole codebase

Run `uv run mypy app/` and `uv run ruff check app/ tests/`. Fix anything that's broken — refactoring across three plans is going to leave a few stragglers (orphan imports, unused parameters from deleted code). This task is a sweep.

**Suggested commit:** `chore: final mypy + ruff sweep after v4 migration`

---

## Definition of Done — Plan C

- [ ] `google-play-scraper` integration ingests reviews from top-N apps per category daily
- [ ] iOS RSS connector implemented and gated behind `enable_ios_rss` flag
- [ ] NIM `LLMAdapter` and `EmbeddingAdapter` working; cloud deployment with `LLM_PROVIDER=nim` is feasible
- [ ] Weekly re-cluster pass runs on schedule; merges/splits work correctly
- [ ] All v3 code removed; `app/agents/`, `app/forecasting/`, `app/ingestion/appstore_mock_connector.py`, `data/mock/`, `data/niches.yaml` are gone
- [ ] ADR-009, 010, 011 written
- [ ] `devtrend-project-document.md` reflects v4; v3 archived under `docs/archive/v3/`
- [ ] README, roadmap, evaluation-plan updated
- [ ] Full test suite green, mypy clean, ruff clean
- [ ] End-to-end integration test (`test_fresh_deploy`) passes

---

## Risks & Mitigations (Plan C specific)

| Risk | Mitigation |
|---|---|
| `google-play-scraper` breaks on Play Store HTML changes | Connector behind same `BaseConnector` interface; failure logs `playstore_likely_throttled` and aborts gracefully. iOS RSS provides fallback signal source. Phase 1.5 can swap in a paid provider via Apptopia / Sensor Tower. |
| NIM rate limit on free tier exhausts during heavy daily extraction | Daily-incremental volume is bounded (a few hundred SourceItems/day typically). If hit, expand `extraction_state='no_signal'` short-circuit usage and consider caching at item level. Free-tier exhaustion → fall back to local Ollama via env switch. |
| Embedding-dim mismatch between Ollama (768) and NIM (1024) | Per-candidate `embedding_model` filter prevents cross-dim matching. Documented; reembedding script is a follow-up if needed. |
| Re-cluster pass merges legitimate distinct candidates | Threshold 0.88 is conservative; tune via the `merge_threshold` config after watching the system for a few weeks. Merges are reversible: archive flag + `merged_into_id` can be unset manually if needed. |
| v3 test deletion accidentally removes a still-relevant test | The deletion sweep should be done in chunks with full-suite green between each. If a v4 test depends on a v3 fixture, it will fail loudly on the next chunk's deletion — fix the fixture before continuing. |
| Project doc rewrite drift from spec | Cross-reference the spec section IDs in each major project-doc section. Future spec changes still have a clear pointer. |

---

## Post-Plan-C: What's deferred

After Plan C lands, v4 is feature-complete per the spec. Items still deferred:

- **Stack Overflow connector** (Phase 1.5) — extraction-flavoured.
- **Google Trends connector** (Phase 1.5) — Demand-flavoured signal.
- **Per-prompt regression CI gate** (spec §12 risk #2) — fixture set for "should-extract" / "should-skip" items, run on prompt changes.
- **Web dashboard** (Phase 2) — when Telegram-only stops scaling.
- **Multi-user / per-user candidate subscriptions** (Phase 2).
- **Postgres + pgvector migration** (Phase 2) — when NumPy brute-force stops being adequate.
- **Specificity gate calibration via accumulated CandidateFeedback** — once enough labels exist, train a small classifier or tune the gate threshold from precision/recall curves.

---

*End of Plan C.*
