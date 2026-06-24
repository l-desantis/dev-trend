# DevTrend

> **v4.C — Feature complete.** Play Store reviews, NVIDIA NIM adapters, weekly re-cluster, and v3 decommissioning are all done.

[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-pytest-green?style=flat-square)](tests/)

An **opportunity discovery engine** for indie developers. DevTrend continuously ingests developer chatter (Reddit, HN, GitHub, Play Store reviews), extracts pain-points via LLM, clusters them into app-opportunity hypotheses, scores them, and delivers the top candidates via a Telegram-first interface — no editorial curation required.

---

## What it is

DevTrend monitors raw developer and user signals, extracts structured pain-points using an LLM pass, and surfaces persistent `OpportunityCandidate` rows ranked by momentum, GitHub validation, and recency. Candidates lifecycle through `emerging → validated → stale` states; humans provide 👍/👎 feedback to improve the signal over time.

---

## Quick start (dev)

**Prerequisites:** Ollama running locally with `qwen2.5` and `nomic-embed-text` pulled.

```bash
# 1. Install dependencies
uv sync

# 2. Create .env (copy from .env.example and fill in tokens)
cp .env.example .env

# 3. Run schema migration (adds embedding_model + merged_into_id columns)
uv run python scripts/migrate_to_v4_2.py --confirm

# 4. Backfill 30 days of history (local Ollama)
uv run python scripts/run_backfill.py --history-days 30 --llm-provider ollama

# 5. Start the app
uv run uvicorn app.main:app --reload
```

The app starts the Telegram bot, the APScheduler jobs, and the FastAPI health endpoint on port 8000.

### Run with Docker Compose (local)

DevTrend ships with a production-shape `docker-compose.yml` and a dev-friendly `docker-compose.override.yml`. With Docker Desktop (or any modern Docker engine) installed:

```bash
cp .env.example .env       # fill in TELEGRAM_BOT_TOKEN, NIM/OpenAI keys, etc.
mkdir -p data/dev
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

The override file bind-mounts `./app/` into the container so code edits are picked up after a `docker compose restart app`. The SQLite DB lives at `./data/dev/devtrend.db` on the host.

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
Ingestion (Reddit / HN / GitHub / Play Store)
      │
      ▼  SourceItem rows
Pipeline  (daily 03:30 UTC)
  1. LLM extract pain-points
  2. Embed (Ollama or NIM)
  3. Identity resolve (cosine ≥ 0.65)
  4. HDBSCAN cluster → OpportunityCandidate
  5. LLM label clusters
  6. GitHub validation
  7. Score + lifecycle
  8. Brief generation
      │
      ▼  Weekly (Sun 04:00 UTC)
  Re-cluster pass (merge drifted / split overbroad)
      │
      ▼
Telegram push (daily digest 08:00 UTC + lifecycle alerts)
```

---

## Configuration

Copy `.env.example` to `.env`. Key variables:

```
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
```

### Cloud deployment with NIM

Set `LLM_PROVIDER=nim`, `EMBEDDING_PROVIDER=nim`, and `NIM_API_KEY=<your-key>`. The NIM adapters hit `https://integrate.api.nvidia.com/v1` by default. Ollama is not needed for cloud runs.

### Cloud deployment with OpenAI

Set `LLM_PROVIDER=openai`, `EMBEDDING_PROVIDER=openai`, and `OPENAI_API_KEY=<your-key>`. Defaults to `gpt-4.1-nano` for extraction/labelling and `text-embedding-3-small` (1536-dim) for embeddings. Override with `OPENAI_LLM_MODEL` and `OPENAI_EMBEDDING_MODEL`. OpenAI and NIM embedding buckets are isolated — switching providers does not invalidate existing cached pain-points.

---

## Sources

- **Reddit** — subreddits: startups, SideProject, Entrepreneur, reactnative, androiddev, iOSProgramming, AppIdeas.
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

## Play Store smoke check

Re-run after any `google-play-scraper` upgrade:

```bash
uv run python scripts/playstore_spike.py
```

---

## Testing

```bash
uv run pytest                          # unit tests (fast)
uv run pytest -m integration           # full e2e walkthrough (slow)
uv run mypy app/
uv run ruff check app/ tests/
```

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

- Reddit: 1000-post ceiling per subreddit per backfill run.
- Play Store scraper: `google-play-scraper` is a community port — Play Store DOM changes can break it without warning. The pinned smoke-check script catches breakage early.
- Play Store TOS: scraping reviews may violate Google's Terms of Service. Use at your own risk.
- Scoring weights (momentum 0.41 / validation 0.35 / novelty 0.24) are calibration placeholders; tune after accumulating feedback.
- Embedding-dim mismatch (Ollama 768-dim vs NIM 1024-dim) means cross-provider identity resolution is disabled by design. A one-off re-embed script is a future follow-up.
