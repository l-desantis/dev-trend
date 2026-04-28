# DevTrend v4 — Opportunity Discovery Pivot

> **Date:** 2026-04-28
> **Status:** Design — pending implementation plan
> **Scope:** Replaces the v3 niche-scoring pipeline with a pain-point-driven candidate discovery pipeline. Clean break from v3 (no users, local deployment).

---

## 1. Motivation

v3 (M1–M6) shipped a working pipeline that monitors developer/market signals across four sources, scores curated niches on Growth/Demand/Novelty, and surfaces a daily Telegram digest of "rising niches." The output is *a generic hint*: "AI-Powered Habit Trackers are hot."

The product goal has shifted: DevTrend should be an **opportunity discovery engine** that surfaces specific, mid-precision app hypotheses ("a habit tracker for ADHD adults that integrates with calendar blocking — repeated complaint across r/ADHD and HN") rather than a trending-topic monitor. The unit of analysis moves from `Niche` (curated, fixed) to `OpportunityCandidate` (emergent, persistent, evidence-backed).

This spec documents the v4 redesign. It is a clean break from v3 — there are no production users to migrate.

---

## 2. Decisions captured during brainstorming

These were resolved one at a time before this design was drafted:

| # | Decision | Rationale |
|---|---|---|
| 1 | **Clean break** from v3, not parallel pipelines | Local-only deployment, no users, simpler |
| 2 | **Niches → Category tag** (~6 coarse categories, replaces 22 fine-grained niches) | Categories are useful for filtering; fine niches were the "generic hint" being escaped |
| 3 | **NumPy brute-force embeddings** behind an `EmbeddingIndex` interface | <10k vectors at v4 scale; swap to sqlite-vec/pgvector later is a one-file change |
| 4 | **Env-switched LLM provider** — Ollama (dev) / NIM (cloud), single adapter handles all jobs | Single mental model, no per-job split |
| 5 | **HN split at ingestion**: Ask HN + comments → extraction; Show HN → validation; news → ignored | Different post types carry different signal shape |
| 5b | **Play Store reviews as real extraction connector**, replaces mock | Reviews are gold for unmet-need extraction |
| 6 | **Daily digest + lifecycle transition alerts**, capped by `MAX_ALERTS_PER_DAY` | Daily digest gives rhythm; lifecycle alerts give the "something changed" signal |
| 7 | **Always-on 👍/👎 inline buttons** on every push, stored as `CandidateFeedback` | Cheap to add; gives a labelled dataset from day one without forcing user behaviour |

Plus one operational pattern added during section 5:

| # | Decision | Rationale |
|---|---|---|
| 8 | **Backfill is provider-independent**, runnable from any host against any DB. Local Ollama can backfill production DBs. | Backfill is one-shot/high-volume (free local LLM is the right fit); steady-state is incremental/low-volume (NIM free tier covers it) |

---

## 3. Architecture & pipeline

### 3.1 Data flow

```
┌──────────────────── INGESTION ────────────────────┐
│ Reddit              (extraction)                  │
│ HN: Ask + comments  (extraction)                  │
│ HN: Show HN         (validation)                  │
│ Play Store reviews  (extraction)  ← replaces mock │
│ iOS RSS reviews     (extraction, optional)        │
│ GitHub repos        (validation)                  │
└─────────────────────────┬─────────────────────────┘
                          ▼
   ┌───────────────────────────────────────┐
   │ 1. EXTRACT  (LLM per item)            │
   │   skip if no unmet-need signal        │
   │   → PainPoint records                 │
   └─────────────────┬─────────────────────┘
                     ▼
   ┌───────────────────────────────────────┐
   │ 2. EMBED  (batched)                   │
   │   PainPoint.embedding                 │
   └─────────────────┬─────────────────────┘
                     ▼
   ┌───────────────────────────────────────┐
   │ 3. RESOLVE IDENTITY                   │
   │   cosine_sim ≥ THRESHOLD              │
   │   → attach to existing Candidate      │
   │   else → unmatched bucket             │
   └─────────────────┬─────────────────────┘
                     ▼
   ┌───────────────────────────────────────┐
   │ 4. CLUSTER UNMATCHED                  │
   │   k-means / HDBSCAN                   │
   │   each cluster ≥ MIN_CLUSTER_SIZE     │
   │   → new OpportunityCandidate          │
   └─────────────────┬─────────────────────┘
                     ▼
   ┌───────────────────────────────────────┐
   │ 5. LABEL  (LLM per new cluster)       │
   │   problem_statement, audience,        │
   │   why_now, specificity (1–5)          │
   └─────────────────┬─────────────────────┘
                     ▼
   ┌───────────────────────────────────────┐
   │ 6. VALIDATE                           │
   │   GitHub repo search + Show HN match  │
   │   → CandidateValidation snapshot      │
   └─────────────────┬─────────────────────┘
                     ▼
   ┌───────────────────────────────────────┐
   │ 7. SCORE                              │
   │   5 dimensions, percentile-normalised │
   │   → CandidateScoreHistory             │
   └─────────────────┬─────────────────────┘
                     ▼
   ┌───────────────────────────────────────┐
   │ 8. LIFECYCLE TRANSITION               │
   │   derive state from score history     │
   │   emit alert if state changed         │
   └─────────────────┬─────────────────────┘
                     ▼
   ┌───────────────────────────────────────┐
   │ 9. BRIEF  (LLM, top-N only)           │
   │   runs at digest time, not scoring    │
   │   stored as CandidateBrief            │
   └───────────────────────────────────────┘
```

### 3.2 Trigger model

| Stage(s) | When |
|---|---|
| Ingestion | Existing v3 cron cadence (GitHub/HN every 6h, Reddit every 12h, Play Store nightly) |
| Stages 1–8 | Daily scoring cron (replaces `_scoring_job`); chains lifecycle alerts in-process |
| Stage 9 (briefs) | Daily digest cron at 08:00 UTC, just-in-time on top-N |
| Weekly re-cluster housekeeping | Sunday 04:00 UTC (after pruning at 03:00) — re-clusters all PainPoints in rolling window, merges drifted candidates, splits over-broad ones |

### 3.3 What is removed from v3

| Removed | Replacement |
|---|---|
| `Niche` (22 entries) | `Category` (~6 entries) |
| `NicheSignal` | Not replaced — raw evidence lives on `PainPoint` |
| `NicheScoreHistory` | `CandidateScoreHistory` |
| `OpportunityBrief` | `CandidateBrief` |
| `_scoring_job` (per-niche) | Daily scoring job over candidates |
| Spike alerts on niche delta | Lifecycle transition alerts on candidates |
| LangGraph agent (`fetcher → … → reviewer`) | Explicit pipeline stages — ADR-005 reversed |
| `data/niches.yaml` (22 niches w/ keywords) | `data/categories.yaml` (~6 categories, no keywords — extraction is LLM-driven) |
| `app/agents/`, `app/forecasting/scoring.py`, v3 bot handlers | New modules under `app/pipeline/`, `app/scoring/`, `app/bot/v4_handlers.py` |

### 3.4 What is retained

- `app/features/trend_features.py` — `linear_regression_slope` and `percentile_rank` are reused for Momentum and dimension normalisation.
- `app/ingestion/` connectors for GitHub, HN, Reddit (with role-tagging extension)
- `MaintenanceState` — `last_pruned_at` semantics extend to v4 entities
- `app/llm/base.py` adapter pattern, extended (see §6)
- M5.5's `bulk_backfill` orchestrator pattern, extended to run the extraction pipeline (see §8)

---

## 4. Data model

### 4.1 New / modified entities

```
Category
  id, slug, name                   -- coarse: wellness, devtools, etc. (~6 rows)

SourceItem  (existing, modified)
  - drop niche_id
  - add category_id              (nullable, assigned at ingestion via heuristic)
  - add role                     ('extraction' | 'validation' | 'ignored')
  - add extraction_state         ('pending' | 'extracted' | 'no_signal' | 'failed')

PainPoint  (NEW)
  id
  source_item_id                 FK → SourceItem (a SourceItem can yield 0..N PainPoints)
  problem_text                   str  -- LLM-extracted, 1 sentence
  audience                       str
  urgency_cue                    str
  current_workaround             str | None
  embedding                      JSON (list[float])
  extracted_at                   datetime
  extractor_model                str  -- e.g. "qwen2.5", "llama-3.x-70b" (audit + cache key)
  candidate_id                   FK → OpportunityCandidate (nullable)

OpportunityCandidate  (NEW — primary output unit)
  id
  category_id                    FK → Category (nullable)
  problem_statement              str  -- LLM-labelled, mid-precision
  audience                       str
  why_now                        str
  centroid                       JSON (list[float]) -- recomputed on evidence change
  specificity                    int  -- 1–5, set at label time
  created_at                     datetime
  last_evidence_at               datetime
  lifecycle_state                str  -- 'emerging' | 'hot' | 'saturated' | 'dormant' | None
  is_archived                    bool
  merged_into_id                 FK → OpportunityCandidate (nullable; merge bookkeeping)
  labeller_model                 str

CandidateValidation  (NEW — GitHub + Show HN snapshot)
  id
  candidate_id                   FK
  repo_count                     int
  top_repos_json                 JSON  -- [{name, stars, url, language}] up to 5
  star_delta_30d                 int
  show_hn_count                  int
  show_hn_top_json               JSON
  validated_at                   datetime

CandidateScoreHistory  (NEW)
  id
  candidate_id                   FK
  score_total                    float    -- 0–100
  score_breakdown_json           JSON     -- {frequency, momentum, source_diversity, validation, specificity}
  scored_at                      datetime

CandidateBrief  (NEW)
  id
  candidate_id                   FK
  headline                       str
  summary                        str  -- MarkdownV2-safe
  evidence_json                  JSON  -- denormalised: top PainPoints with quotes + source links
  validation_summary             str
  generated_at                   datetime
  model_name                     str

CandidateFeedback  (NEW — thumbs-up/down)
  id
  candidate_id                   FK
  brief_id                       FK → CandidateBrief (nullable)
  chat_id                        int
  user_id                        int | None
  label                          str  -- 'up' | 'down'
  created_at                     datetime
  UNIQUE (candidate_id, user_id, brief_id)
```

### 4.2 Relationships

```
SourceItem ──1:N──▶ PainPoint ──N:1──▶ OpportunityCandidate ──1:N──▶ CandidateScoreHistory
                                                              ──1:N──▶ CandidateBrief
                                                              ──1:N──▶ CandidateValidation
                                                              ──1:N──▶ CandidateFeedback
                                                              ──N:1──▶ Category
```

### 4.3 Identity & merge bookkeeping

- A new `PainPoint` starts with `candidate_id = NULL`.
- Stage 3 (resolve identity) attaches it to an existing candidate if `cosine_sim(painpoint.embedding, candidate.centroid) ≥ IDENTITY_RESOLUTION_THRESHOLD`.
- Unattached pain-points enter stage 4 (cluster unmatched). Singletons that don't form a cluster ≥ `CLUSTERING_MIN_CLUSTER_SIZE` stay unattached and are revisited at the weekly re-cluster pass.
- When the weekly re-cluster job decides candidate A and B are the same, A is archived (`is_archived = True`, `merged_into_id = B.id`). B inherits A's PainPoints by updating each `PainPoint.candidate_id = B.id` (explicit row update, not just a soft pointer). B's `created_at` is set to `min(A.created_at, B.created_at)` to preserve the "gaining steam for N weeks" story. B's centroid is recomputed from the merged evidence set.
- Queries that surface candidates to users filter `is_archived = False`. Historical queries (e.g. score history of a now-merged candidate) traverse `merged_into_id` chains via a helper: `resolve_candidate_root(id)`.

### 4.4 Pruning extension

Extends ADR-008's policy:
- `PainPoint` rows are pruned with their parent `SourceItem` (cascade FK).
- `CandidateValidation` snapshots > 30 days old are pruned (latest snapshot per candidate is always kept).
- `OpportunityCandidate`, `CandidateScoreHistory`, `CandidateBrief`, `CandidateFeedback` are kept forever — same rationale as v3 score history (load-bearing for momentum and longitudinal stories).

---

## 5. Scoring & lifecycle

### 5.1 Five dimensions

All 0–100, percentile-normalised over a rolling 30-day window across **all active candidates**:

| Dimension | Weight | Raw input |
|---|---|---|
| Frequency | 0.25 | `count(PainPoint where candidate_id=X and extracted_at >= now-30d)` |
| Momentum | 0.30 | 7-day rolling slope on daily PainPoint attachment count (reuses `linear_regression_slope`) |
| Source Diversity | 0.15 | `len(distinct(source_type, subreddit_or_app))` over evidence; capped at 8 |
| Validation | 0.20 | Non-monotonic curve over GitHub repo data (see §5.2) |
| Specificity | 0.10 | LLM-judged at label time (1–5); refreshed only on relabel |

`total_score = sum(dim_value × weight)` ∈ [0, 100].

Initial weights are calibration guesses. Will need tuning once the system has produced ≥30 days of real candidate scores. Weights are configurable in `config.py`.

### 5.2 Validation curve (non-monotonic)

```
repo_count == 0                                  → Validation = 30   (green-field-risky)
1 ≤ repo_count ≤ 5,  no repo > 5k stars          → Validation = 90   (sweet spot — validated, not saturated)
6 ≤ repo_count ≤ 20, no repo > 20k stars         → Validation = 70   (validated, getting crowded)
repo_count > 20  OR  any repo > 20k stars        → Validation = 30   (saturated, major incumbents)
```

The brief prose surfaces the nuance ("3 small OSS repos validate demand; no major incumbent yet"). The number alone is intentionally not enough — readers need the prose.

### 5.3 Lifecycle states

Recomputed at every scoring run from the candidate's score history over the last 14 days. The recomputed value is written to `OpportunityCandidate.lifecycle_state` so the bot can read it without re-deriving, and so the next run can compare today's value to yesterday's persisted value to detect transitions.

| State | Trigger |
|---|---|
| `emerging` | momentum ≥ 60, frequency < 30, age < 14 days |
| `hot` | momentum ≥ 60, frequency ≥ 30 |
| `saturated` | frequency ≥ 70, momentum < 30 |
| `dormant` | no new PainPoints in 14 days |
| `None` | none of the above; score still computed, no transition alert |

A change in `lifecycle_state` between two consecutive scoring runs fires a **lifecycle transition alert**.

### 5.4 Specificity gate

Candidates with `specificity ≤ SPECIFICITY_GATE` (default 2):
- do **not** get a brief generated
- do **not** appear in `/opportunities` or `/emerging`
- do **not** fire transition alerts

They sit in the DB as "weak clusters" and get a relabel attempt at the weekly re-cluster pass — when a low-specificity candidate has accumulated new evidence since its last labelling, the re-cluster job re-invokes the labeller (stage 5) on the full evidence set; if the new specificity rises above the gate, the candidate becomes user-visible. This is the primary defence against LLM extraction failures producing plausible-but-vague candidates.

### 5.5 Spike alerts retired

v3's `SPIKE_ALERT_THRESHOLD` mechanism is gone. Lifecycle transitions are the user-meaningful alert: instead of "this niche jumped 15 points," the user gets "**Habit tracker for ADHD adults** transitioned from emerging → hot — 6 new pain points this week from 3 distinct sources."

`MAX_ALERTS_PER_DAY` (default 3) caps lifecycle alerts per day. Over-cap transitions are logged silently and surfaced in the next morning's digest as "+N other transitions overnight, see `/opportunities`."

---

## 6. Adapters & configuration

### 6.1 Adapter interfaces

```python
class LLMAdapter(ABC):
    async def extract_pain_point(self, source_item: SourceItem) -> PainPointDraft | None:
        """Returns extracted fields, or None if no unmet-need signal."""

    async def label_cluster(self, pain_points: list[PainPoint]) -> ClusterLabel:
        """Returns problem_statement, audience, why_now, specificity (1–5)."""

    async def generate_brief(
        self, candidate: OpportunityCandidate, evidence: list[PainPoint]
    ) -> str:
        """Returns MarkdownV2-safe brief summary."""


class EmbeddingAdapter(ABC):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Batched embedding."""
```

### 6.2 Provider matrix

| Provider | LLM model | Embedding model | Selected when |
|---|---|---|---|
| Ollama (dev/local) | `qwen2.5` | `nomic-embed-text` | `LLM_PROVIDER=ollama` |
| NVIDIA NIM (cloud) | `meta/llama-3.x-70b-instruct` | `nvidia/nv-embedqa-e5-v5` | `LLM_PROVIDER=nim` |
| Mock (tests) | deterministic fixture | hash-based fixed vectors | `LLM_PROVIDER=mock` |

Adapter selection happens at startup in `app/llm/factory.py` and the chosen adapters are injected into the pipeline stages — same DI pattern M4 used.

### 6.3 New config keys (added to `.env.example` and `config.py`)

```env
# Provider selection
LLM_PROVIDER=ollama          # ollama | nim | mock
EMBEDDING_PROVIDER=ollama    # ollama | nim | mock

# NVIDIA NIM (cloud)
NIM_API_KEY=
NIM_LLM_MODEL=meta/llama-3.x-70b-instruct
NIM_EMBEDDING_MODEL=nvidia/nv-embedqa-e5-v5

# Pipeline tuning
EXTRACTION_BATCH_SIZE=20
EMBEDDING_BATCH_SIZE=64
IDENTITY_RESOLUTION_THRESHOLD=0.82
CLUSTERING_MIN_CLUSTER_SIZE=3
SPECIFICITY_GATE=2
MAX_ALERTS_PER_DAY=3
WEEKLY_RECLUSTER_CRON_HOUR=4
WEEKLY_RECLUSTER_CRON_DAY=sun

# Play Store
PLAYSTORE_TOP_N_PER_CATEGORY=50
PLAYSTORE_REVIEWS_PER_APP=200
PLAYSTORE_REFRESH_CRON_HOUR=2
PLAYSTORE_REFRESH_CRON_DAY=mon
```

### 6.4 Extraction cache (load-bearing)

There is no separate cache table. The cache *is* the `PainPoint` rows themselves, indexed on `(source_item_id, extractor_model)`. Stage 1 logic:

```
for source_item in pending_items:
    if exists(PainPoint where source_item_id=item.id and extractor_model=current_model):
        skip                                         # already extracted by this model
    if source_item.extraction_state == 'no_signal' and not force:
        skip                                         # already determined empty by this or a prior model
    extract → write PainPoint(s) or set extraction_state='no_signal'
```

Behavior:

- A SourceItem already extracted by `qwen2.5` is **not** re-extracted by `llama-3.x-70b` on the next daily run unless `--force` is passed (this is intentional — see §8).
- Re-extracting a window with a different provider is a deliberate, scripted operation: `scripts/run_extraction.py --provider=nim --since=2026-04-01 --force`.
- The `extraction_state='no_signal'` short-circuit prevents the LLM from being called repeatedly on items that have nothing to extract — by far the largest cost-saving in the pipeline.
- This makes the local-Ollama → cloud-NIM operational pattern (§8) cheap and idempotent.

---

## 7. Bot UX & feedback

### 7.1 Command set

| Command | Description |
|---|---|
| `/start`, `/help` | Welcome + command list |
| `/opportunities` | Top N candidates by current score, MarkdownV2 list |
| `/opportunity <id>` | Full candidate view: problem, audience, why-now, evidence quotes, validation summary, lifecycle state, score breakdown, 👍/👎 buttons |
| `/categories` | List Categories with active candidate counts |
| `/category <slug>` | Top candidates in that category |
| `/emerging` | Only candidates in `emerging` state — the discovery feed |
| `/sources` | Last ingestion timestamp + status per connector (kept from v3) |

**Removed from v3:** `/briefing` (renamed to `/opportunities`), `/niches`, `/niche <slug>`, `/trending` (replaced by `/emerging` + lifecycle alerts).

**Stretch:** `/triage` — guided thumbs-up/down flow over unreviewed candidates. Only ship if the always-on buttons turn out to give too sparse a signal.

### 7.2 Push flows

- **Daily digest** at 08:00 UTC — top 3 candidates by score, MarkdownV2 formatted, each with 👍/👎 buttons.
- **Lifecycle transition alerts** — fire from the scoring job (chained, in-process). Capped at `MAX_ALERTS_PER_DAY`.
- **No spike alerts.**

### 7.3 Inline feedback buttons

```
┌────────────────────────────────────────┐
│ 1. Habit tracker for ADHD adults       │
│ Score: 78  ·  🔥 Hot                   │
│ "Repeated complaint across r/ADHD..."  │
│ Sources: r/ADHD, HN, 3 Play Store apps │
│ Validation: 3 small repos, no major    │
│   incumbent.                           │
│                                        │
│   [ 👍 useful ]  [ 👎 not useful ]    │
│   [ 📄 details ]                       │
└────────────────────────────────────────┘
```

Implementation:
- `callback_data = "fb:up:42"` / `"fb:down:42"`
- Allowlist middleware applies to callbacks (same as commands)
- Insert `CandidateFeedback`; uniqueness `(candidate_id, user_id, brief_id)` — re-clicking flips label
- Update message inline keyboard to show "✓ marked useful" for visual confirmation
- Reply via `answerCallbackQuery` ("Thanks — recorded.")

### 7.4 MarkdownV2 formatter

Reused from v3 mostly as-is. New helper: `lifecycle_arrow(state)` → 🌱 emerging / 🔥 hot / 🛑 saturated / 💤 dormant.

4096-char limit still applies; `/opportunity <id>` truncates evidence list with "…and N more pieces of evidence" footer.

---

## 8. Backfill (provider-independent)

Backfill is the highest-volume LLM consumer in the system: a fresh DB needs ingestion + extraction over ~30 days of historical data. This is a one-shot operation, not a steady-state cost.

**Pattern:**

```
scripts/run_backfill.py \
    --db-url=sqlite:///./devtrend.db \
    --llm-provider=ollama \
    --history-days=30
```

- CLI flags override env vars — backfill can target any DB the running machine can reach.
- Reuses pipeline stages 1–7 as a one-shot pass over the historical window (no daily cron involved).
- Production deployment never *needs* to run extraction at full volume: it inherits backfill output and only extracts daily-incremental SourceItems via NIM.
- Idempotent: existing `(source_type, external_id)` uniqueness on SourceItem; extraction cache key on `(source_item_id, extractor_model)` prevents re-extraction.

Risk #3 ("cold-start") is reframed: a fresh deploy expects a pre-populated DB from a local backfill run. This is documented operational requirement, not a runtime concern.

A future tool, `scripts/run_extraction.py --provider=nim --since=DATE --force`, lets you re-extract a window with a different model — useful for spot-checking quality differences or recovering from a buggy prompt. Out of v4 implementation scope; the pipeline structure must allow it as a one-line script later.

---

## 9. Testing strategy

Each pipeline stage is independently testable via `MockLLMAdapter` + `MockEmbeddingAdapter`. Reuses M6's testing patterns (VCR-style HTTP fixture replay for connectors, deterministic mocks for LLM/embedding).

| Test | Subject |
|---|---|
| `test_extract_stage` | Fixture SourceItems → only those with unmet-need text produce PainPoints |
| `test_identity_resolution` | Pre-seeded candidates with known centroids: similarity 0.85 → attaches; 0.70 → unmatched |
| `test_clustering` | 9 unmatched pain-points → 2 clusters of size ≥3; 3 singletons remain unmatched |
| `test_validation_curve` | repo_count = 0 / 3 / 15 / 50 → validation score = 30 / 90 / 70 / 30 |
| `test_lifecycle_transition` | Forced score history shape → state transitions emit alerts at the right moment |
| `test_specificity_gate` | Specificity=2 candidate → no brief, no alert, no listing; lives in DB |
| `test_feedback_idempotency` | Same user clicks 👍 twice → one row; clicks 👎 after 👍 → label flips |
| `test_merge_preserves_history` | A merged into B → A.created_at preserved; queries traverse `merged_into_id` |
| `test_extraction_cache` | Re-running extraction with same `(item, model)` → no re-call; different model → re-extracted |
| `test_pipeline_e2e_with_mocks` | Full daily pipeline on fixture corpus → CandidateBrief produced for top-N |

The **mock historical replay harness** from M6 is adapted to replay SourceItems through the v4 pipeline with mocked LLM/embedding outputs to verify deterministic reproducibility.

Real connectors (Play Store, NIM) are tested with VCR HTTP-fixture replay — same pattern as the existing connector tests.

---

## 10. Decommissioning v3

Since this is a clean break with no production users:

1. **Database migration** — `scripts/migrate_to_v4.py` drops the v3 tables (`niches`, `niche_signals`, `niche_score_history`, `opportunity_briefs`) and creates the v4 tables. Not Alembic — `Base.metadata.drop_all` + `create_all`, gated behind `--confirm`. `MaintenanceState.last_pruned_at` is preserved.

2. **Code deletion** — remove `app/agents/`, `app/forecasting/scoring.py`, v3 bot handlers in `app/bot/handlers.py`, v3 notification builders in `app/bot/notifications.py`. Keep `app/features/trend_features.py`.

3. **YAML** — `data/niches.yaml` is replaced by `data/categories.yaml` (~6 entries: slug, name, description; no keywords).

4. **ADRs** — three new entries in `docs/decisions.md`:
   - **ADR-009: Pivot to opportunity discovery** — captures the why, references this spec
   - **ADR-010: Retire LangGraph in favour of explicit pipeline stages** — reverses ADR-005
   - **ADR-011: Identity resolution & weekly re-clustering** — threshold, merge bookkeeping, weekly housekeeping

5. **Documentation** —
   - `devtrend-project-document.md` bumped to v4. v3 sections moved to `docs/archive/v3/` (not deleted — historical context).
   - `README.md` updated for new commands, providers, backfill workflow
   - `docs/roadmap.md` updated: Phase 1.5 partly absorbed; Stack Overflow + Google Trends still pending; Prophet superseded by Momentum
   - `docs/evaluation-plan.md` updated with v4-specific evaluation criteria (extraction precision, cluster coherence, candidate stability over weeks)

---

## 11. Roadmap impact

| Item | v3 status | v4 status |
|---|---|---|
| Google Trends connector | Phase 1.5 deferred | Still deferred — could feed Momentum as secondary signal |
| Stack Overflow connector | Phase 1.5 deferred | Still deferred — extraction source candidate |
| Prophet forecasting | Phase 1.5 deferred | Likely never landed — Momentum operates on shorter windows per-candidate; ROI unclear |
| Competition dimension | Phase 1.5 deferred | **Replaced** by Validation dimension |
| App Store mock | Phase 1 mock | **Replaced** by real Play Store reviews connector in v4 |
| pgvector / Postgres | Phase 2 deferred | Pulled forward conceptually (NumPy is fine for v4 scale; documented swap point) |

---

## 12. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | LLM cost at full extraction volume on cloud | (a) cheap regex/keyword pre-filter before sending to LLM; (b) extraction cache by `(source_item_id, extractor_model)`; (c) backfill via local Ollama, only daily-incremental on cloud |
| 2 | Prompt drift — extractor quality is now load-bearing, no automatic regression detection | Phase 2: build a small fixture set of "should-extract" / "should-skip" items as CI check. Out of v4 scope. |
| 3 | Cold-start: fresh DB needs full backfill including extraction pass | Reframed as operational requirement — backfill via local Ollama before deploy. CLI is provider-independent. |
| 4 | Reddit 1000-post-per-sub ceiling (carried from ADR-007) | Logged as `oldest_item_age_days` per sub; documented partial coverage |
| 5 | Cluster quality / vague candidates reaching users | Specificity gate at label time (≤2 → not surfaced); weekly re-cluster relabel attempt |
| 6 | Identity resolution drift over time (centroids stop matching past evidence) | Weekly re-cluster pass merges/splits; `merged_into_id` preserves history |
| 7 | Play Store scraper TOS/breakage risk (`google-play-scraper`) | Connector behind same `BaseConnector` interface; can be swapped for paid provider in Phase 1.5+. iOS RSS provides fallback signal source. |
| 8 | Initial scoring weights are guesses | Documented as calibration TODO; weights configurable; revisit after ≥30 days of real candidate data |

---

## 13. Open questions deferred to implementation plan

- Concrete clustering algorithm choice (k-means vs HDBSCAN vs simple agglomerative) — to be picked during implementation based on what pain-point distributions look like in practice.
- Embedding dimensionality and exact model versions — depend on Ollama/NIM availability at implementation time.
- `SourceItem.role` resolution rules for HN (Show HN detection currently relies on title prefix; comments need a separate fetch path).
- Telegram inline-button retry behaviour when the message is older than 48 hours (Telegram's edit window).

These are surfaced in the implementation plan, not resolved here.

---

*End of v4 design spec.*
