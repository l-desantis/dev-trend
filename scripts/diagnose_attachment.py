"""Identity-resolution attachment diagnostic.

Answers the question behind "results never change": why does stage-3 identity
resolution attach almost nothing, leaving the candidate corpus fragmented?

It measures the *actual* cosine geometry of the production embedding space
(`nvidia/nv-embedqa-e5-v5` in prod) against the configured
`identity_resolution_threshold` (0.82, originally tuned for Ollama
`nomic-embed-text`). If the near-duplicate similarities sit well below 0.82,
the threshold is the root cause and this script tells you where to set it.

Read-only. Safe to run against the production DB.

Usage (local SQLite default):
    uv run python -m scripts.diagnose_attachment

Usage (explicit DB, wider window, cap geometry sample):
    uv run python -m scripts.diagnose_attachment --db-url sqlite+aiosqlite:///./data/dev/devtrend.db --days 30 --sample 3000

On the VPS (inside the app container):
    docker compose exec app python -m scripts.diagnose_attachment --days 30
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import numpy as np

# Thresholds to sweep when reporting "how many pain points would attach".
_DEFAULT_THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.82, 0.85, 0.90]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose why identity resolution attaches (almost) nothing."
    )
    parser.add_argument("--days", type=int, default=30, help="Look-back window for pain points (default 30).")
    parser.add_argument("--sample", type=int, default=3000, help="Max pain points used for the O(n^2) geometry check (default 3000).")
    parser.add_argument("--top", type=int, default=10, help="How many top-scored candidates to dump (default 10).")
    parser.add_argument("--db-url", default=None, help="Override DATABASE_URL.")
    return parser.parse_args()


def _hr(label: str) -> None:
    print(f"\n{'─' * 64}")
    print(f"  {label}")
    print(f"{'─' * 64}")


def _percentiles(name: str, values: np.ndarray) -> None:
    if values.size == 0:
        print(f"  {name}: (no data)")
        return
    pct = {p: float(np.percentile(values, p)) for p in (5, 25, 50, 75, 90, 95, 99)}
    print(
        f"  {name}: n={values.size}  "
        f"min={values.min():.3f}  p5={pct[5]:.3f}  p25={pct[25]:.3f}  "
        f"p50={pct[50]:.3f}  p75={pct[75]:.3f}  p90={pct[90]:.3f}  "
        f"p95={pct[95]:.3f}  p99={pct[99]:.3f}  max={values.max():.3f}"
    )


async def _run(args: argparse.Namespace) -> None:
    if args.db_url:
        import os
        os.environ["DATABASE_URL"] = args.db_url

    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from app.config import get_settings
    from app.db import _get_session_factory, reset_engine
    from app.models import CandidateScoreHistory, OpportunityCandidate, PainPoint
    from app.pipeline.embedding_index import EmbeddingIndex

    get_settings.cache_clear()
    settings = get_settings()
    threshold = settings.identity_resolution_threshold

    reset_engine()
    session_factory = _get_session_factory()

    since = datetime.now(UTC) - timedelta(days=args.days)

    async with session_factory() as session:
        # --- Active candidates with a centroid (exactly what identity res loads) ---
        candidates = (
            await session.execute(
                select(OpportunityCandidate)
                .where(OpportunityCandidate.is_archived.is_(False))
                .where(OpportunityCandidate.centroid.is_not(None))
            )
        ).scalars().all()

        # --- Recent pain points with embeddings ---
        recent_pps = (
            await session.execute(
                select(PainPoint)
                .where(PainPoint.extracted_at >= since)
                .where(PainPoint.embedding.is_not(None))
            )
        ).scalars().all()

        # --- Centroid lookup for ALL candidates (true-positive calibration) ---
        # Attached pain points may belong to archived candidates, so this is not
        # restricted to the active set loaded above.
        centroid_rows = (
            await session.execute(
                select(OpportunityCandidate.id, OpportunityCandidate.centroid)
                .where(OpportunityCandidate.centroid.is_not(None))
            )
        ).all()
        centroid_by_id = {cid: cen for cid, cen in centroid_rows}

        # --- Top candidates by latest score (what the digest actually surfaces) ---
        latest_at = (
            select(
                CandidateScoreHistory.candidate_id,
                func.max(CandidateScoreHistory.scored_at).label("latest_at"),
            )
            .group_by(CandidateScoreHistory.candidate_id)
            .subquery()
        )
        top_rows = (
            await session.execute(
                select(OpportunityCandidate, CandidateScoreHistory.score_total)
                .join(latest_at, OpportunityCandidate.id == latest_at.c.candidate_id)
                .join(
                    CandidateScoreHistory,
                    (CandidateScoreHistory.candidate_id == latest_at.c.candidate_id)
                    & (CandidateScoreHistory.scored_at == latest_at.c.latest_at),
                )
                .where(OpportunityCandidate.is_archived.is_(False))
                .order_by(CandidateScoreHistory.score_total.desc())
                .limit(args.top)
            )
        ).all()

    # ------------------------------------------------------------------ summary
    _hr("Corpus summary")
    print(f"  identity_resolution_threshold (config) : {threshold}")
    print(f"  embedding_provider                     : {settings.embedding_provider}")
    print(f"  look-back window                       : {args.days} days")
    print(f"  active candidates with centroid        : {len(candidates)}")
    print(f"  recent pain points (embedded)          : {len(recent_pps)}")

    unattached = [pp for pp in recent_pps if pp.candidate_id is None]
    attached = len(recent_pps) - len(unattached)
    print(f"    ↳ already attached                   : {attached}")
    print(f"    ↳ unattached                         : {len(unattached)}")

    by_model_c: dict[str | None, int] = {}
    for c in candidates:
        by_model_c[c.embedding_model] = by_model_c.get(c.embedding_model, 0) + 1
    by_model_pp: dict[str | None, int] = {}
    for pp in recent_pps:
        by_model_pp[pp.embedding_model] = by_model_pp.get(pp.embedding_model, 0) + 1
    print(f"  candidate embedding_model buckets      : {by_model_c}")
    print(f"  pain-point embedding_model buckets     : {by_model_pp}")
    if set(by_model_c) != set(by_model_pp):
        print("  *** NOTE: candidate vs pain-point buckets differ — cross-bucket items can NEVER match. ***")

    # Build per-model candidate indexes (mirrors identity_resolution.py).
    cands_by_model: dict[str | None, list[OpportunityCandidate]] = {}
    for c in candidates:
        cands_by_model.setdefault(c.embedding_model, []).append(c)
    index_by_model = {
        model: EmbeddingIndex(ids=[c.id for c in cs], vectors=[c.centroid for c in cs])
        for model, cs in cands_by_model.items()
    }

    # --------------------------- (1) unattached pain point -> nearest candidate
    _hr("(1) Unattached pain point → nearest candidate centroid")
    sims: list[float] = []
    no_bucket = 0
    for pp in unattached:
        idx = index_by_model.get(pp.embedding_model)
        if idx is None or len(idx) == 0:
            no_bucket += 1
            continue
        res = idx.nearest(pp.embedding, k=1, threshold=0.0)
        if res:
            sims.append(res[0][1])
    sims_arr = np.asarray(sims, dtype=np.float32)
    if no_bucket:
        print(f"  {no_bucket} unattached pain points have NO candidate in their embedding bucket.")
    _percentiles("nearest-candidate cosine", sims_arr)

    if sims_arr.size:
        print("\n  Attachment recovery — how many WOULD attach at each threshold:")
        total = sims_arr.size
        for t in args.thresholds:
            n = int((sims_arr >= t).sum())
            bar = "█" * int(40 * n / total)
            flag = "  ← current" if abs(t - threshold) < 1e-9 else ""
            print(f"    t={t:.2f}  {n:5d}/{total:<5d} ({100*n/total:5.1f}%)  {bar}{flag}")

    # ----------------- (2) attached pain point -> its OWN candidate centroid (TP band)
    _hr("(2) Attached pain point → its OWN candidate centroid (true-positive band)")
    tp_sims: list[float] = []
    tp_missing = 0
    attached_pps = [pp for pp in recent_pps if pp.candidate_id is not None]
    for pp in attached_pps:
        cen = centroid_by_id.get(pp.candidate_id)
        if cen is None:
            tp_missing += 1
            continue
        v = np.asarray(pp.embedding, dtype=np.float32)
        c = np.asarray(cen, dtype=np.float32)
        nv = float(np.linalg.norm(v))
        nc = float(np.linalg.norm(c))
        if nv == 0.0 or nc == 0.0:
            tp_missing += 1
            continue
        tp_sims.append(float(v @ c / (nv * nc)))
    tp_arr = np.asarray(tp_sims, dtype=np.float32)
    if tp_missing:
        print(f"  {tp_missing} attached pain points skipped (candidate has no centroid).")
    _percentiles("own-centroid cosine", tp_arr)

    if tp_arr.size:
        print("\n  True-positive retention — how many ALREADY-attached pain points would")
        print("  STILL clear each threshold (want this high while section (1) stays low):")
        total = tp_arr.size
        for t in args.thresholds:
            n = int((tp_arr >= t).sum())
            bar = "█" * int(40 * n / total)
            flag = "  ← current" if abs(t - threshold) < 1e-9 else ""
            print(f"    t={t:.2f}  {n:5d}/{total:<5d} ({100*n/total:5.1f}%)  {bar}{flag}")
        print(
            "\n  Reading: the correct threshold sits BELOW this band (so true positives\n"
            "  attach) but ABOVE the false-positive scale in section (3). The gap\n"
            "  between the two is your usable margin.\n"
            "  Caveat: each centroid is the MEAN of its members and still includes\n"
            "  this pain point, so these sims are upward-biased — most for small\n"
            "  candidates. Treat this band as an optimistic ceiling, not the exact cut."
        )

    # ------------------------- (3) raw geometry: nearest *other* pain point
    _hr("(3) Embedding geometry — nearest OTHER pain point (near-duplicate scale)")
    pool = recent_pps
    if len(pool) > args.sample:
        rng = np.random.default_rng(0)
        pick = rng.choice(len(pool), size=args.sample, replace=False)
        pool = [pool[i] for i in pick]
    if len(pool) < 2:
        print("  Not enough pain points to measure.")
    else:
        M = np.asarray([pp.embedding for pp in pool], dtype=np.float32)
        norms = np.linalg.norm(M, axis=1, keepdims=True)
        M = M / np.where(norms == 0, 1.0, norms)
        nn = np.empty(M.shape[0], dtype=np.float32)
        # Chunked to keep the full NxN matrix off the heap for large N.
        chunk = 512
        for start in range(0, M.shape[0], chunk):
            block = M[start:start + chunk] @ M.T          # (chunk, N)
            for r in range(block.shape[0]):
                block[r, start + r] = -1.0                 # mask self
            nn[start:start + block.shape[0]] = block.max(axis=1)
        _percentiles("nearest-neighbour cosine", nn)
        print(
            "\n  Reading: if even the single most-similar other pain point typically\n"
            f"  scores below {threshold} (the current threshold), then 0.82 is simply\n"
            "  unreachable in this embedding space — the threshold, not the data, is the bug."
        )

    # ------------------------------------ (4) what the digest actually surfaces
    _hr(f"(4) Top {args.top} candidates by latest score (what you see each day)")
    if not top_rows:
        print("  No scored candidates found.")
    else:
        for c, score in top_rows:
            le = c.last_evidence_at.date().isoformat() if c.last_evidence_at else "never"
            lc = c.lifecycle_state or "—"
            ps = (c.problem_statement or "")[:58]
            print(
                f"  id={c.id:<5} score={score:6.2f}  spec={c.specificity}  "
                f"lifecycle={lc:<10} last_evidence={le:<10} {ps}"
            )

    print()


def main() -> None:
    args = _parse_args()
    args.thresholds = _DEFAULT_THRESHOLDS
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
