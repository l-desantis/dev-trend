# DevTrend Roadmap

---

## v4 — Opportunity Discovery (current)

**Goal:** Reframe DevTrend from "trending niche tracker" to "opportunity discovery engine." Mid-precision app hypotheses, persistent candidates, GitHub-as-validator, Play Store reviews as primary extraction source.

| Plan | Focus | Status |
|---|---|---|
| v4.A | Foundation & Pipeline Core | ✅ Complete |
| v4.B | Scoring, Lifecycle, Bot UX, Feedback | ✅ Complete |
| v4.C | Play Store, NIM, Re-cluster, v3 Decommissioning | ✅ Complete |

See `docs/superpowers/specs/2026-04-28-opportunity-discovery-pivot-design.md` for full design.

---

## Phase 1 — Core MVP (v1–v3) ⬛ Superseded by v4

Phase 1 (milestones M1–M6) delivered a working niche-scoring system and Telegram bot. It has been superseded by v4, which replaces the niche taxonomy approach with emergent candidate discovery.

Historical project document: `docs/archive/v3/devtrend-project-document-v3.md`.

---

## Phase 1.5 — Signal Expansion

**Goal:** fill in deferred sources; improve extraction coverage.

**Trigger:** v4 stable (all Plan C checklist items green) and ≥30 days of real candidate history available.

### Items

- **Stack Overflow connector** — Stack Exchange API, tag question volumes. Extraction-flavoured source.
- **Google Trends connector** — search-interest velocity as Demand-dimension signal.
- **Per-prompt regression CI gate** — fixture set for "should-extract" / "should-skip" items, run on every prompt change to catch extraction regressions early.
- **Specificity gate calibration** — once enough `CandidateFeedback` labels exist, train a small classifier or tune the specificity threshold from precision/recall curves.
- **Reddit UA policy review** — confirm UA string is compliant and requests are not rate-limited.

---

## Phase 2 — Scale and Intelligence

**Goal:** production-grade infrastructure, semantic retrieval, multi-user support.

### Items

- Replace SQLite with PostgreSQL + pgvector for native vector indexing (pgvector pulled forward conceptually but not implemented — NumPy brute-force is adequate for Phase 1 volumes).
- Add web dashboard (FastAPI + Jinja2 or React) when Telegram-only stops scaling.
- Multi-user Telegram support with per-user candidate subscriptions.
- ARIMA / deep forecasting beyond rolling slope.
- Containerise with Docker Compose → AKS Helm charts.
- Swap to Apptopia / Sensor Tower for Play Store data if `google-play-scraper` becomes unreliable long-term.
