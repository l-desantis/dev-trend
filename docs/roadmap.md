# DevTrend Roadmap

---

## Phase 1 — Core MVP (current)

**Goal:** end-to-end pipeline: ingest → score → brief → Telegram.

**Sources:** GitHub, Hacker News, Reddit, App Store mock (four connectors).
**Forecasting:** Rolling 7-day slope for Growth dimension.
**Scoring:** Three dimensions — Growth 0.41 / Demand 0.35 / Novelty 0.24.
**Interface:** Telegram bot with 7 commands + daily digest + daily spike alert.

### Milestones

| Milestone | Focus |
|---|---|
| M1 — Foundation | FastAPI, SQLite, bot /start + /help, ADRs, gitignore, MockLLMAdapter |
| M2 — Ingestion | Four connectors, niches.yaml taxonomy, APScheduler |
| M3 — Scoring | Rolling slope, percentile normalisation, NicheScoreHistory |
| M4 — Agent Graph | LangGraph five-node pipeline, Ollama (qwen2.5), brief persistence |
| M5 — Full Bot | All seven commands, daily digest, spike alert, allowlist middleware |
| M6 — Hardening | Full test suite, pruning job, replay harness, docs complete |

**Definition of Done:** see §18 of `devtrend-project-document.md`.

---

## Phase 1.5 — Signal Expansion

**Goal:** fill in deferred sources and forecasting; reintroduce the Competition dimension.

**Trigger:** Phase 1 ships and is stable (all M6 checklist items green).

### Items

- **Google Trends connector** — use the official Google Trends API (alpha). Adds search-interest velocity as a signal for the Demand dimension.
- **Stack Overflow connector** — Stack Exchange API, tag question volumes and growth rate proxies. Adds developer-adoption signal.
- **Prophet forecasting** — revisit when ≥30 days of real NicheSignal history is available. Replace the rolling-slope Growth computation; weights to be re-tuned on real data.
- **Competition dimension** — reintroduce when a real app-store data provider is integrated. New formula: `(growth × w1) + (demand × w2) + (novelty × w3) − (competition × w4)`. Weights TBD after calibration.
- **Reddit User-Agent policy review** — confirm UA string is compliant and requests are not being rate-limited.

---

## Phase 2 — Scale and Intelligence

**Goal:** production-grade infrastructure, semantic retrieval, multi-user support.

### Items

- Replace SQLite with PostgreSQL + pgvector for embedding support.
- Add vector embedding layer for semantic niche retrieval.
- Swap Ollama adapter for OpenAI or Anthropic hosted model.
- Add web dashboard (FastAPI + Jinja2 or React).
- LangGraph conditional edges: reviewer retry loop, parallel source fetches, cheap-vs-expensive LLM branch.
- ARIMA / deep forecasting beyond Prophet.
- Containerise with Docker Compose → AKS Helm charts.
- Multi-user Telegram support with per-user niche subscriptions.
