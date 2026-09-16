# DevTrend

> **v4.D — Stable production.** Identity resolution calibrated for NVIDIA NIM embeddings, lifecycle fixes applied, and daily digest evolution restored.

[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/license-Apache%20License%202.0-blue)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-pytest-green?style=flat-square)](tests/)

An **opportunity discovery engine** for indie developers. DevTrend continuously ingests developer chatter (Reddit, HN, GitHub, Play Store reviews), extracts pain-points via LLM, clusters them into app-opportunity hypotheses, scores them, and delivers the top candidates via a Telegram-first interface — no editorial curation required.

---

## What it is

DevTrend is a **staged pipeline monolith driven by a scheduler** — not a microservice system. Ingestion connectors write `SourceItem` rows; the pipeline then runs as an ordered sequence of stages (extract → embed → identity-resolve → cluster → label), followed by scoring, GitHub validation and Telegram delivery. APScheduler orchestrates ingestion, the pipeline, scoring, the daily digest and pruning.

The output is a set of persistent `OpportunityCandidate` rows ranked by momentum, GitHub validation and recency. Candidates lifecycle through `emerging → validated → stale`; humans provide 👍/👎 feedback to improve the signal over time.

### Design rule

**The LLM is used only where it is the best available tool; everything that must be reproducible is deterministic code.**

- LLM does: unstructured text → structured `PainPoint` records under a strict Pydantic schema, and naming of clusters.
- Deterministic code does: embedding, identity resolution, clustering, scoring, lifecycle and ranking.

Aggregation and ranking are therefore reproducible run-to-run, instead of being re-litigated by a model each night.

---

## Quick start (dev)

**Prerequisites:** Docker (for PostgreSQL) and Ollama running locally with `qwen2.5` and `nomic-embed-text` pulled.

```bash
# 1. Install dependencies
uv sync

# 2. Create .env (copy from .env.example and fill in tokens)
cp .env.example .env

# 3. Start PostgreSQL
docker compose up -d postgres

# 4. Apply schema migrations
uv run alembic upgrade head

# 5. Backfill 30 days of history (local Ollama)
uv run python scripts/run_backfill.py --history-days 30 --llm-provider ollama

# 6. Start the app
uv run uvicorn app.main:app --reload
```

The app starts the Telegram bot, the APScheduler jobs, and the FastAPI health endpoint on port 8000.

### Run with Docker Compose (local)

DevTrend ships with a production-shape `docker-compose.yml` (PostgreSQL 16 + a one-shot `alembic upgrade head` migrate service + the app) and a dev-friendly `docker-compose.override.yml`. With Docker Desktop (or any modern Docker engine) installed:

```bash
cp .env.example .env       # fill in TELEGRAM_BOT_TOKEN, NIM/OpenAI keys, etc.
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

The app waits for the migrate service to complete successfully before starting. The override file bind-mounts `./app/` into the container so code edits are picked up after a `docker compose restart app`. Database state lives in the `devtrend_pgdata` volume.

To stop:

```bash
docker compose down
```

To run the production-shape stack (no source bind-mount, image pulled from `ghcr.io/l-desantis/dev-trend`):

```bash
docker compose -f docker-compose.yml up -d
```

---

## Bot commands

| Command | Description |
|---|---|
| `/start` | Welcome + quick-start |
| `/help` | Show all commands |
| `/opportunities` | Top-N candidates by score |
| `/opportunity <id>` | Detail view for one candidate |
| `/categories` | List all categories |
| `/category <slug>` | Candidates by category |
| `/emerging` | Candidates in `emerging` state |
| `/sources` | Ingestion status per source |

---

## Architecture

```
Ingestion connectors (GitHub / Hacker News / Reddit RSS / Play Store)
      │  windowed + paginated on backfill, single page on normal runs
      │  dedup at the DB level: UNIQUE (source_type, external_id) + ON CONFLICT DO NOTHING
      ▼  SourceItem rows
Pipeline  (daily 03:30 UTC) — ordered stages
  1. LLM extract       → PainPoint records under a strict Pydantic schema
  2. Embed             → Ollama / NIM / OpenAI, per-provider buckets
  3. Identity resolve  → cosine ≥ 0.65, attach to existing candidates
  4. Cluster           → HDBSCAN over the remaining pain-points → new OpportunityCandidate
  5. LLM label         → name + summarise each cluster
  6. GitHub validation
  7. Score + lifecycle → explicit weighted heuristic, no model in the loop
  8. Brief generation
      │
      ▼  Weekly (Sun 04:00 UTC)
  Re-cluster pass (merge drifted / split overbroad)
      │
      ▼
Telegram push (daily digest 08:00 UTC + lifecycle alerts)
```

Ingestion details:

- **GitHub** — repository search API.
- **Hacker News** — Algolia API (Ask HN + Show HN).
- **Reddit** — per-subreddit RSS feeds.
- **Play Store** — reviews via `google-play-scraper`.
- HTTP retry and exponential backoff on 429/5xx is centralised in `app/ingestion/http_utils.py`, so connectors do not each reinvent it.

### LLM layer

Four providers sit behind a factory, selected by `LLM_PROVIDER`:

| Provider | Default model | Notes |
|---|---|---|
| `ollama` | `qwen2.5` | Local default; JSON mode + Pydantic validation |
| `openai` | `gpt-4.1-nano` | Structured output via the `parse` API with a Pydantic response model |
| `nim` | NVIDIA NIM endpoints | Cloud, OpenAI-compatible |
| `mock` | — | Deterministic stub used by the test suite |

Structured output is enforced, not hoped for: either the provider's native schema-constrained parse, or JSON mode followed by Pydantic validation. Prompts live in a dedicated module as templates rather than inline strings.

### Engineering & infrastructure

- **PostgreSQL 16** with async SQLAlchemy and **Alembic** migrations.
- **Docker Compose** stack: `postgres` → `migrate` (`alembic upgrade head`) → `app`, with health checks and ordered startup.
- **Test suite** (pytest) covering the pipeline, LLM adapters, ingestion connectors, scoring and the Telegram bot; a `mock` provider keeps tests offline and deterministic.
- **SOPS + age** encrypted secrets committed as `secrets.enc.env`.
- **Five GitHub Actions workflows**: `ci`, `build-and-push`, `deploy` (health-gated, auto-rollback), `rollback` (manual, by SHA) and `prune-ghcr`.
- Conventional commits and feature branches throughout the history.

---

## Recent Improvements

### Identity Resolution Calibration (June 2026)
- **Threshold optimized**: 0.82 → 0.65 for NVIDIA NIM embeddings
- **Problem**: Original threshold (0.82) was calibrated for Ollama's nomic-embed-text (768-dim) but too high for NVIDIA's nv-embedqa-e5-v5 (1024-dim)
- **Impact**: Pain points now correctly attach to existing candidates instead of creating duplicate themes daily
- **Verification**: 96.6% attachment rate with proper discrimination

### Lifecycle Bug Fix
- **Issue**: Candidates with `last_evidence_at = NULL` were treated as age 0 → "immortal hot" state
- **Fix**: Fallback to `created_at` for age calculation when evidence is missing
- **Result**: Stale candidates now properly transition to dormant state

### Performance & Stability
- **Embedding isolation**: Ollama vs NIM vs OpenAI embeddings kept in separate buckets
- **Weekly re-clustering**: Merges drifted candidates, splits over-broad clusters
- **Monster cluster detection**: Archive candidates with >100 pain points and low cohesion

---

## Configuration

Copy `.env.example` to `.env`. Key variables:

```
DATABASE_URL=postgresql+asyncpg://devtrend:devtrend@localhost:5432/devtrend
LLM_PROVIDER=ollama          # ollama | nim | openai | mock
EMBEDDING_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
NIM_API_KEY=                 # required when LLM_PROVIDER=nim
OPENAI_API_KEY=              # required when LLM_PROVIDER=openai
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
GITHUB_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=

# v4 pipeline settings
EXTRACTION_BATCH_SIZE=20
EMBEDDING_BATCH_SIZE=64
IDENTITY_RESOLUTION_THRESHOLD=0.65  # cosine similarity for attaching to existing candidates
CLUSTERING_MIN_CLUSTER_SIZE=3
SPECIFICITY_GATE=2
MAX_ALERTS_PER_DAY=3
```

### Cloud deployment with NIM

Set `LLM_PROVIDER=nim`, `EMBEDDING_PROVIDER=nim`, and `NIM_API_KEY=<your-key>`. The NIM adapters hit `https://integrate.api.nvidia.com/v1` by default. Ollama is not needed for cloud runs.

**Important**: NIM uses `nvidia/nv-embedqa-e5-v5` (1024-dim) embeddings. The identity resolution threshold (0.65) is calibrated for this model. If switching from Ollama (768-dim), existing pain points remain in their original embedding bucket and won't match NIM-embedded candidates.

### Cloud deployment with OpenAI

Set `LLM_PROVIDER=openai`, `EMBEDDING_PROVIDER=openai`, and `OPENAI_API_KEY=<your-key>`. Defaults to `gpt-4.1-nano` for extraction/labelling and `text-embedding-3-small` (1536-dim) for embeddings. Override with `OPENAI_LLM_MODEL` and `OPENAI_EMBEDDING_MODEL`. OpenAI and NIM embedding buckets are isolated — switching providers does not invalidate existing cached pain-points.

**Note**: Each embedding provider (Ollama, NIM, OpenAI) maintains separate embedding buckets. Cross-provider identity resolution is disabled by design. For production consistency, pick one provider and stick with it.

---

## Sources

- **Reddit** — per-subreddit RSS feeds: startups, SideProject, Entrepreneur, reactnative, androiddev, iOSProgramming, AppIdeas.
- **Hacker News** — Ask HN + Show HN items via Algolia.
- **GitHub** — Star growth on repos matching candidate keywords (validation signal).
- **Play Store** — Reviews via `google-play-scraper==1.2.7` (pinned). 57 seeded apps across 6 categories in `data/playstore_seed_apps.yaml`. Update the YAML to add/remove apps; the weekly discovery job re-reads it.
- **iOS App Store** — Optional. Set `ENABLE_IOS_RSS=true` to activate; requires `ios_app_id` populated on `TrackedApp` rows.

> **Reddit note:** The `REDDIT_USER_AGENT` must follow Reddit's API rules. Default: `DevTrend/4.0 (by /u/yourhandle)`. Update with your handle.

---

## Backfill workflow

On first launch with an empty DB, a backfill runs automatically (`BACKFILL_ON_EMPTY=true`). For dev or recovery:

```bash
# Local Ollama backfill (default)
uv run python scripts/run_backfill.py --history-days 30

# Cap items per source (faster for testing)
uv run python scripts/run_backfill.py --history-days 7 --max-extraction-items 50
```

After backfill, candidates are available immediately. Switch to NIM for production incremental runs by setting `LLM_PROVIDER=nim` in `.env` — Ollama-embedded pain-points stay in place; new ones use NIM embeddings in their own `embedding_model` bucket.

---

## Diagnostics & Monitoring

### Identity Resolution Health Check
To verify identity resolution is working correctly (especially after switching embedding providers):

```bash
# Check attachment rates and threshold calibration
uv run python -m scripts.diagnose_attachment --days 7

# Sample output shows:
# - Current threshold value
# - Attachment rate for recent pain points
# - True-positive retention band
# - Nearest-neighbor similarity distribution
```

### Common Issues & Solutions

1. **Low attachment rate (< 50%)**:
   - Threshold may be too high for your embedding model
   - Run `diagnose_attachment` to see similarity distributions
   - Adjust `IDENTITY_RESOLUTION_THRESHOLD` in `.env`

2. **Over-merging (one candidate absorbs everything)**:
   - Threshold may be too low
   - Check for "monster candidates" with >100 pain points
   - Archive overly broad candidates: `UPDATE opportunity_candidates SET is_archived = true WHERE id = <monster_id>`

3. **Same themes appear daily**:
   - Identity resolution not attaching pain points to existing candidates
   - Verify threshold is calibrated for your embedding model
   - Check embedding provider consistency (don't mix Ollama/NIM/OpenAI)

4. **Candidates never go dormant**:
   - Ensure `last_evidence_at` fallback to `created_at` is working
   - Check lifecycle.py handles NULL `last_evidence_at` correctly

---

## Play Store smoke check

Re-run after any `google-play-scraper` upgrade:

```bash
uv run python scripts/playstore_spike.py
```

---

## Testing

```bash
uv run pytest                          # unit tests (fast, LLM_PROVIDER=mock)
uv run pytest -m integration           # full e2e walkthrough (slow)
uv run mypy app/
uv run ruff check app/ tests/
uv run alembic upgrade head            # apply migrations
```

The suite covers the pipeline stages, LLM adapters, ingestion connectors, scoring and the bot. It does **not** measure extraction or clustering *quality* — see [Limitations](#limitations).

---

## Production deploy & secrets

DevTrend deploys automatically to a single Hetzner CX22 VPS on every push to `main`. The deploy is health-gated: if `/health` fails to come up within 60 s, the previous image is restored automatically and a Telegram message reports the rollback.

### Secrets (SOPS + age)

Production secrets are SOPS-encrypted at `secrets.enc.env` in this repo. The matching age private key lives on the VPS at `/etc/devtrend/age.key`. To edit secrets:

```bash
sops secrets.enc.env
```

This opens `$EDITOR` with the decrypted content; saving re-encrypts on close. Commit the updated `secrets.enc.env` and push — the next deploy will pick up the new values.

To add a contributor with edit access: append their age public key to `.sops.yaml`, then run `sops updatekeys secrets.enc.env`.

### Manual operations

- **Roll back to a previous build:** GitHub → Actions → "Manual Rollback" → enter the target short SHA (any `sha-*` tag still on `ghcr.io/l-desantis/dev-trend`).
- **Re-deploy a SHA:** same workflow.
- **First-time VPS setup:** see `docs/superpowers/runbooks/vps-bootstrap.md`.

### Image retention

The most recent 10 `sha-*` builds plus `latest` are kept on ghcr.io. Older builds are pruned weekly by `prune-ghcr.yml`.

---

## Limitations

- **No labelled evaluation set.** The test suite is unit + integration only: it verifies that the pipeline behaves correctly, not that extraction or clustering is *good*. There is no annotated corpus and no precision/recall measurement for pain-point extraction or cluster coherence. This is the biggest open gap.
- **Clustering is pragmatic, not tuned.** HDBSCAN is imported at runtime and used if present; if the library is unavailable the code falls back to `AgglomerativeClustering`. The HDBSCAN call passes only `min_cluster_size` and `metric="euclidean"` — `min_samples` and `cluster_selection_method` are left at defaults.
- **Metric inconsistency.** Clustering runs on Euclidean distance while identity resolution uses cosine similarity. On normalised embeddings the two are monotonically related, but the mismatch is deliberate-by-omission rather than by design, and it is worth resolving.
- Reddit: RSS feeds cap history, and backfill is bounded per subreddit per run.
- Play Store scraper: `google-play-scraper` is a community port — Play Store DOM changes can break it without warning. The pinned smoke-check script catches breakage early.
- Play Store TOS: scraping reviews may violate Google's Terms of Service. Use at your own risk.
- Scoring weights (momentum 0.41 / validation 0.35 / novelty 0.24) are calibration placeholders; tune after accumulating feedback.
- Embedding-dim mismatch (Ollama 768-dim vs NIM 1024-dim vs OpenAI 1536-dim) means cross-provider identity resolution is disabled by design. A one-off re-embed script is a future follow-up.
- **Resolved**: Identity resolution threshold now correctly calibrated at 0.65 for NIM embeddings (was 0.82 for Ollama).
