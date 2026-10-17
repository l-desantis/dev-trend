# Post Staleness-Fix — Next Steps

> Follow-up roadmap after the identity-threshold + lifecycle fix
> (branch `fix/staleness-identity-threshold-lifecycle`, plan:
> `docs/superpowers/plans/2026-06-24-staleness-fix-identity-threshold-lifecycle.md`).

**Context recap.** The "digest never changes for a month" symptom was root-caused to
`identity_resolution_threshold = 0.82` (tuned for Ollama `nomic-embed-text`) being
unreachable in production's `nvidia/nv-embedqa-e5-v5` cosine geometry. Stage-3
identity resolution therefore almost never attached pain points to existing
candidates, so recurring themes re-spawned as duplicate candidates daily, and a
lifecycle bug (`None last_evidence_at` treated as age 0) kept evidence-less
candidates immortally "hot". The shipped fix: threshold → 0.65 + lifecycle fallback
to `created_at`. **Deployment is fully automated** (push `main` → CI → Build and Push
→ Deploy; deploy regenerates `.env` from `secrets.enc.env` and uses the code default,
so no manual prod env edit is needed).

The steps below are **strictly ordered** — Step 1 gates everything after it. Do not
stack scoring/clustering changes before observing what the threshold fix alone does.

---

## Step 1 — Verify the fix in production (GATE)

The 0.65 threshold is *observable, not final*. After the first nightly pipeline run
(03:30 UTC) post-deploy:

- **Attachment rate** should climb from ~0 to a meaningful number:
  ```bash
  docker compose logs app --since 24h | grep identity_resolution_complete
  ```
  Read the `attached` field — it was ~0 for a month; it should now be non-trivial.
- **Re-measure geometry** to confirm the unattached backlog shrinks and
  `last_evidence_at` starts populating:
  ```bash
  docker compose exec app python -m scripts.diagnose_attachment --days 7
  ```
- **Digest movement** over ~3–5 days: rankings should shift as evidence compounds and
  the old evidence-less leaders (ids 237 / 239 / 236 / 242 …) age into `dormant`
  (the 14-day lifecycle clock).

**Decision:**
- Attachment climbing + rankings shifting → fix confirmed, proceed to Step 2.
- Attachment still ~0 → 0.65 too high; drop toward 0.62 and re-observe. (Data says
  unlikely: TP band p50 was 0.704.)

---

## Step 2 — Phase 2: backlog de-fragmentation (GATED on a measurement)

The ~167 existing duplicate candidates will **not** auto-consolidate, because
`app/pipeline/recluster.py`'s `merge_threshold = 0.88` is the *same nomic-era
miscalibration* and is almost certainly unreachable under e5 — so the weekly merge
pass fires ~never.

**Do NOT pick a new merge threshold by guessing.** Gate:

1. **Measure.** Extend `scripts/diagnose_attachment.py` with a section that computes
   pairwise cosine between active-candidate centroids (within the single e5 bucket)
   and prints the distribution + the similarity of the visibly-duplicate top pairs.
   Run it against prod.
2. **Recalibrate.** Add `recluster_merge_threshold` to `app/config.py` (defaulting to
   the measured value), thread it into `run_weekly_recluster(merge_threshold=...)`.
3. **Backfill (optional, one-off).** Trigger a single `run_weekly_recluster` pass over
   prod to collapse the duplicate backlog under the corrected threshold.
4. **Docs.** Update the ADR `docs/decisions.md` (it pairs the 0.82 *and* 0.88
   thresholds) with a superseding note covering both recalibrations — this also
   clears the prose-doc Minor the final review flagged.

---

## Step 3 — PR2: drop the `repo_count == 0 → 30` validation floor

Now more worthwhile: that 30-point floor props up the frozen leaders
(`app/scoring/dimensions.py`). But:

- **Re-confirm the premise on *current* data first** (PR2's own plan requires it):
  after Step 1, do candidates still show `repo_count = 0` in the digest?
- Do it **after** Step 1's effect is visible — don't change two scoring levers
  (attachment dynamics + validation floor) at once.

Plan: `docs/superpowers/plans/2026-05-18-validation-pr2-no-signal-scoring.md`.

---

## Step 4 — PR3: stage-4 cohesion gate

Lowest priority. Hygiene against the over-broad "X struggle with Y" clusters; ships
disabled, low risk. Won't move staleness — purely cluster-quality.

Plan: `docs/superpowers/plans/2026-05-18-validation-pr3-cohesion-gate.md`.

---

## Step 5 — Reddit degradation (separate track)

Unrelated to staleness, but a real input-quality regression from the original
diagnosis: Reddit times out and its volume dropped from ~300–400/day to ~50/day since
~2026-06-12. Worth its own investigation once Steps 1–4 settle (likely an expired
token / rate-limit / connector issue).

---

## Sequencing rationale

```
Step 1 (verify)  ──gate──►  Step 2 (de-fragment)
       │                          │
       └────────────────────────► Step 3 (PR2)  ──►  Step 4 (PR3)

Step 5 (Reddit) — independent, schedule when convenient
```

Every downstream tuning decision depends on observing the threshold fix in isolation.
Step 1 is non-negotiable before touching scoring or clustering again.
