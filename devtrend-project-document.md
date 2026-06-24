# DevTrend — Project Document

> **Version:** 4.0
> **Last updated:** May 6, 2026
> **Change log:** v4.0 — Complete rewrite. DevTrend pivots from "trending niche tracker" to opportunity discovery engine. Pain-point extraction, HDBSCAN clustering, `OpportunityCandidate` lifecycle, GitHub-as-validator, Play Store reviews as primary extraction source, NIM cloud adapters, weekly re-cluster pass. v3 code fully removed. See `docs/archive/v3/devtrend-project-document-v3.md` for historical reference.

---

## 1. Overview

DevTrend is an **opportunity discovery engine** for indie developers. It continuously ingests developer chatter (Reddit, HN, GitHub, Play Store reviews) and uses an LLM pipeline to extract pain-points, cluster them into app-opportunity hypotheses, score and lifecycle them, and deliver the top candidates via a Telegram-first interface.

The system is hypothesis-first: instead of requiring a human to curate a list of niches, it discovers candidates automatically from raw signal and surfaces them ranked by momentum, validation, and recency. Human feedback (👍/👎) refines the signal over time.

```
Daily: ingest → extract pain-points → embed → identity-resolve → cluster → label → score → lifecycle → digest push
Weekly (Sun): re-cluster rolling window, prune old data
```

---

## 2. Goals

- Extract structured pain-points from raw developer/user chatter using an LLM extraction pass.
- Cluster pain-points into `OpportunityCandidate` rows using HDBSCAN; label clusters as mid-precision app hypotheses.
- Score candidates on momentum, validation (GitHub stars on related repos), and recency; push top-3 daily.
- Lifecycle candidates through `emerging → validated → stale` states; alert when a candidate transitions.
- Deliver candidates via Telegram bot with on-demand commands, automatic daily digest, and feedback collection.
- Support local-or-cloud deployment: Ollama for dev/backfill, NVIDIA NIM for production.
- Maintain full traceability through structured logging and deterministic scoring.

---

## 3. Non-Goals (deferred)

- Web dashboard or browser-based UI (Phase 2).
- Multi-user / per-user candidate subscriptions (Phase 2).
- Postgres + pgvector migration (Phase 2, when NumPy brute-force stops scaling).
- Stack Overflow connector (Phase 1.5).
- Google Trends connector (Phase 1.5).
- Per-prompt regression CI gate (Phase 1.5).
- Specificity gate calibration from accumulated feedback (once enough labels exist).

---

## 4. Architectural Decisions

| # | Decision | Reference |
|---|---|---|
| ADR-001 | Product name: DevTrend | docs/decisions.md |
| ADR-002 | Single asyncio event loop | docs/decisions.md |
| ADR-003 | SQLite for Phase 1 | docs/decisions.md |
| ADR-004 | Three scoring dimensions, percentile rank | docs/decisions.md |
| ADR-005 | LangGraph (superseded by ADR-010) | docs/decisions.md |
| ADR-006 | Daily digest + spike alert chaining | docs/decisions.md |
| ADR-007 | Bulk backfill on empty DB at startup | docs/decisions.md |
| ADR-008 | Data retention: 90d source / 30d signals | docs/decisions.md |
| ADR-009 | Pivot to opportunity discovery | docs/decisions.md |
| ADR-010 | Retire LangGraph → explicit pipeline stages | docs/decisions.md |
| ADR-011 | Identity resolution + weekly re-cluster + embedding-model isolation | docs/decisions.md |

---

## 5. High-Level Architecture

```
┌─────────────────── Ingestion ───────────────────┐
│  Reddit   HN   GitHub   Play Store   (iOS RSS)  │
└─────────────────┬───────────────────────────────┘
                  │ SourceItem rows (role=extraction)
┌─────────────────▼────────────────────────────────┐
│              Pipeline (daily, 03:30 UTC)          │
│  Stage 1 — Extract pain-points (LLM)              │
│  Stage 2 — Embed pain-points                      │
│  Stage 3 — Identity resolution                    │
│  Stage 4 — Cluster unmatched → new candidates     │
│  Stage 5 — Label new candidates (LLM)             │
│  Stage 6 — Validate (GitHub stars)                │
│  Stage 7 — Score + lifecycle                      │
│  Stage 8 — Brief generation (top candidates)      │
└─────────────────┬────────────────────────────────┘
                  │ OpportunityCandidate rows
┌─────────────────▼────────────────────────────────┐
│           Weekly Re-cluster (Sun 04:00 UTC)       │
│   Merge drifted candidates / split overbroad      │
└──────────────────────────────────────────────────┘
                  │
┌─────────────────▼────────────────────────────────┐
│              Telegram Bot                         │
│   Daily digest push, /opportunities, /category   │
│   /emerging, feedback buttons (👍/👎)             │
└──────────────────────────────────────────────────┘
```

---

## 6. Repository Structure (post-v4)

```
app/
  config.py               — Settings (pydantic-settings)
  db.py                   — SQLAlchemy async engine
  models.py               — ORM: SourceItem, PainPoint, OpportunityCandidate, …
  schemas.py              — Pydantic request/response schemas
  main.py                 — FastAPI app + lifespan
  ingestion/
    base.py               — BaseConnector, NormalizedItem
    reddit_connector.py
    hn_connector.py
    github_connector.py
    playstore_connector.py — Real Play Store reviews (google-play-scraper)
    playstore_app_discovery.py — Static seed loader (data/playstore_seed_apps.yaml)
    ios_rss_connector.py  — Optional iOS RSS (enable_ios_rss=True)
    scheduler.py          — APScheduler jobs
    backfill.py           — Bulk backfill
    http_utils.py
  llm/
    base.py               — LLMAdapter ABC
    embedding_base.py     — EmbeddingAdapter ABC
    prompts.py            — Shared prompt templates
    ollama_adapter.py
    nim_adapter.py        — NVIDIA NIM (cloud)
    nim_embedding_adapter.py
    openai_adapter.py
    anthropic_adapter.py
    mock_adapter.py
    mock_embedding_adapter.py
    factory.py            — make_llm_adapter / make_embedding_adapter
    schemas.py            — PainPointDraft, ClusterLabel
  pipeline/
    orchestrator.py       — run_pipeline() — wires all 8 stages
    extract.py            — Stage 1
    embed.py              — Stage 2
    identity_resolution.py — Stage 3
    clustering.py         — Stage 4
    labelling.py          — Stage 5
    validation.py         — Stage 6
    lifecycle.py          — Stage 7
    brief_generation.py   — Stage 8
    recluster.py          — Weekly re-cluster
    embedding_index.py    — In-memory cosine index
  scoring/
    candidate_scorer.py
    dimensions.py
    normalize.py
  bot/
    bot.py, v4_handlers.py, v4_notifications.py, feedback.py, …
  db_helpers/
    categories.py
    candidate_resolution.py — resolve_candidate_root()
  maintenance/
    pruning.py
data/
  categories.yaml
  playstore_seed_apps.yaml — Curated apps to track (57 apps across 6 categories)
scripts/
  run_backfill.py
  run_scoring.py
  migrate_to_v4_2.py      — Add embedding_model, merged_into_id columns
  playstore_spike.py      — Permanent smoke check for google-play-scraper
docs/
  decisions.md            — ADR-001 through ADR-011
  roadmap.md
  evaluation-plan.md
  archive/v3/             — Historical v3 project doc
  superpowers/specs/      — Full design spec
```

---

## 7. Telegram Bot Design

### Commands

| Command | Description |
|---|---|
| `/start` | Welcome + quick-start |
| `/help` | Show all commands |
| `/opportunities` | Top-N current candidates by score |
| `/opportunity <id>` | Detail view for one candidate |
| `/categories` | List all categories |
| `/category <slug>` | Candidates filtered by category |
| `/emerging` | Candidates in `emerging` lifecycle state |
| `/sources` | Ingestion status per source type |

### Push flows

- **Daily digest** (08:00 UTC): top-3 candidates with brief + feedback buttons.
- **Lifecycle alert**: push when a candidate transitions to `emerging` (score crosses threshold for first time). Capped at `max_alerts_per_day` (default 3).

### Feedback

Inline keyboard buttons `fb:up:<id>` / `fb:down:<id>` create `CandidateFeedback` rows. Feedback is collected but not yet used to retrain scoring weights (deferred to Phase 1.5).

---

## 8. Data Sources

| Source | Role | Notes |
|---|---|---|
| Reddit | extraction | Subreddits: startups, SideProject, Entrepreneur, reactnative, androiddev, iOSProgramming, AppIdeas |
| HN (Hacker News) | extraction | Ask HN + Show HN items |
| GitHub | validation | Star growth on repos matching candidate keywords |
| Play Store | extraction | Reviews via `google-play-scraper==1.2.7` (pinned); 57 seeded apps |
| iOS RSS | extraction (optional) | Apple customer-review RSS; disabled by default (`enable_ios_rss=False`) |

---

## 9. Canonical Data Model

### Core tables

| Table | Description |
|---|---|
| `source_items` | Raw ingested items (posts, reviews, repos). `role=extraction` feeds the LLM pipeline; `role=validation` feeds the GitHub scoring. |
| `pain_points` | Extracted pain-point signals. Each has an `embedding` + `embedding_model` for cross-provider isolation. |
| `opportunity_candidates` | Clustered app-opportunity hypotheses. Carries `centroid`, `embedding_model`, `merged_into_id` (merge chain), `lifecycle_state`. |
| `candidate_validations` | GitHub-based validation snapshots per candidate. |
| `candidate_score_history` | Daily score snapshots (momentum, validation, novelty). |
| `candidate_briefs` | LLM-generated briefs for top candidates. |
| `candidate_feedback` | User 👍/👎 rows. |
| `lifecycle_events` | Audit log of lifecycle state transitions. |
| `tracked_apps` | Play Store (and optional iOS) apps to track for review ingestion. |
| `categories` | Canonical category taxonomy (6 slugs). |
| `maintenance_state` | One-row table tracking last pruning run. |

### Key invariants

- `PainPoint.embedding_model` must match `OpportunityCandidate.embedding_model` for identity resolution.
- `merged_into_id` forms a DAG (cycle detection in `resolve_candidate_root()`).
- `is_archived=True` candidates are never shown in bot output.

---

## 10. Scoring Design

Three dimensions, percentile-ranked over the last 30 days:

| Dimension | Weight | Signal |
|---|---|---|
| Momentum | 0.41 | Pain-point velocity + recent evidence |
| Validation | 0.35 | GitHub stars on related repos |
| Novelty | 0.24 | Candidate age (recency bonus) |

`score_total = 0.41 × momentum_norm + 0.35 × validation_norm + 0.24 × novelty_norm` (all in [0, 100]).

---

## 11. Pipeline Design

Eight stages, composed in `app/pipeline/orchestrator.py`:

| Stage | Module | Description |
|---|---|---|
| 1 — Extract | `extract.py` | LLM classifies each pending `SourceItem` → `PainPoint` or skip |
| 2 — Embed | `embed.py` | Batch-embed pain-points using `EmbeddingAdapter` |
| 3 — Identity | `identity_resolution.py` | Attach unmatched pain-points to nearest candidate (cosine ≥ 0.65) |
| 4 — Cluster | `clustering.py` | HDBSCAN on remaining unmatched → new `OpportunityCandidate` rows |
| 5 — Label | `labelling.py` | LLM labels each unlabelled candidate |
| 6 — Validate | `validation.py` | GitHub stars search for related repos |
| 7 — Score + lifecycle | `lifecycle.py` + `candidate_scorer.py` | Score, state machine, alert trigger |
| 8 — Brief | `brief_generation.py` | LLM generates brief for top-N candidates |

---

## 12. Scheduling Strategy

| Job | Trigger | Description |
|---|---|---|
| `github_ingestion` | Every 6 h | GitHub connector |
| `hn_ingestion` | Every 6 h | HN connector |
| `reddit_ingestion` | Every 12 h | Reddit connector |
| `playstore_ingestion` | Daily 02:00 UTC | Play Store reviews |
| `daily_pipeline` | Daily 03:30 UTC | 8-stage pipeline |
| `daily_scoring` | Daily 04:15 UTC | Score + lifecycle |
| `daily_digest` | Daily 08:00 UTC | Telegram push |
| `playstore_app_discovery` | Mon 02:30 UTC | Re-read seed YAML, upsert TrackedApp |
| `weekly_recluster` | Sun 04:00 UTC | Merge/split candidates |
| `weekly_pruning` | Sun 03:00 UTC | Delete old SourceItems + LifecycleEvents |

---

## 13. Configuration (`.env` keys)

```
# Core
APP_NAME=DevTrend
DATABASE_URL=sqlite+aiosqlite:///./devtrend.db

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ALLOWED_CHAT_IDS=

# LLM
LLM_PROVIDER=ollama          # ollama | nim | mock
EMBEDDING_PROVIDER=ollama    # ollama | nim | mock
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5

# NIM (cloud)
NIM_API_KEY=
NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NIM_LLM_MODEL=meta/llama-3.1-70b-instruct
NIM_EMBEDDING_MODEL=nvidia/nv-embedqa-e5-v5

# Data sources
GITHUB_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=DevTrend/4.0 (by /u/yourhandle)

# Play Store
PLAYSTORE_TOP_N_PER_CATEGORY=50
PLAYSTORE_REVIEWS_PER_APP=200
PLAYSTORE_CRON_HOUR=2

# iOS RSS (optional)
ENABLE_IOS_RSS=false

# Pipeline
IDENTITY_RESOLUTION_THRESHOLD=0.65
CLUSTERING_MIN_CLUSTER_SIZE=3
SPECIFICITY_GATE=2
MAX_ALERTS_PER_DAY=3

# Scheduling
PIPELINE_CRON_HOUR=3
PIPELINE_CRON_MINUTE=30
SCORING_CRON_HOUR=4
SCORING_CRON_MINUTE=15
DIGEST_CRON_HOUR=8
WEEKLY_RECLUSTER_CRON_DAY=sun
WEEKLY_RECLUSTER_CRON_HOUR=4
PRUNING_CRON_HOUR=3
```

---

## 14. Logging and Traceability

Structured JSON logging via `structlog`. Key log events:

- `extraction_batch_complete` — items processed, pain-points found
- `identity_resolution_complete` — attached count
- `clustering_complete` — candidates created, noise
- `labelling_complete` — candidates labelled
- `scoring_complete` — score distribution
- `lifecycle_transition` — old/new state, alert flag
- `digest_pushed` — recipients, top-N
- `playstore_fetch_failed` — app_id, error
- `playstore_likely_throttled` — abort signal
- `weekly_recluster_complete` — merged/split/relabelled counts

---

## 15. Evaluation Strategy

See `docs/evaluation-plan.md` for the full checklist. Key v4 review criteria:

- Extraction precision: ≥60% of extracted pain-points are genuine unmet needs (spot-check 20 per run).
- Clustering coherence: candidate problem statements are specific enough to be actionable (specificity ≥ 3).
- Lifecycle correctness: `emerging` candidates reflect genuine signal spikes, not noise.
- Bot UX: digest push is parseable and actionable within 30 seconds.

---

## 16. Risk Register

| Risk | Mitigation |
|---|---|
| `google-play-scraper` breaks (Play Store DOM change) | `scripts/playstore_spike.py` is a permanent smoke check; re-run on every dependency bump. iOS RSS provides fallback. |
| NIM rate limits on free tier | Free-tier volume is bounded; fall back to local Ollama via env switch. |
| Embedding-dim mismatch (Ollama 768 vs NIM 1024) | `embedding_model` filter on all matching; documented in ADR-011. |
| Re-cluster merges legitimate distinct candidates | Threshold 0.88 is conservative; tune via config. Merges are reversible (`merged_into_id` can be unset). |
| LLM extraction over-selects noise | `specificity_gate` filters clusters with specificity < 2 before surfacing. |

---

## 17. Implementation Roadmap

| Plan | Focus | Status |
|---|---|---|
| v4.A | Foundation & Pipeline Core | Complete |
| v4.B | Scoring, Lifecycle, Bot UX, Feedback | Complete |
| v4.C | Play Store, NIM, Re-cluster, v3 Decommissioning | In Progress → Complete |

See `docs/superpowers/plans/` for detailed task breakdowns.

---

## 18. Definition of Done — v4

- [ ] All 8 pipeline stages run end-to-end on real data with Ollama.
- [ ] Play Store reviews ingested daily from seeded app list.
- [ ] NIM adapters work; `LLM_PROVIDER=nim` is viable for cloud deployment.
- [ ] Weekly re-cluster pass merges/splits correctly.
- [ ] Telegram bot: digest push, all commands, feedback buttons working.
- [ ] All v3 code removed from disk.
- [ ] ADR-009, 010, 011 written.
- [ ] Full test suite green; mypy clean; ruff clean.
- [ ] End-to-end integration test passes (`@pytest.mark.integration`).
