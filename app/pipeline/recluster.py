"""Weekly re-cluster pass — spec §4.3 + identity-resolution drift mitigation."""
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OpportunityCandidate, PainPoint
from app.pipeline.clustering import _cluster_labels

log = structlog.get_logger(__name__)


@dataclass
class ReclusterReport:
    merged_count: int = 0
    split_count: int = 0
    relabelled_count: int = 0
    embedding_models_processed: list[str] = field(default_factory=list)
    duration_ms: int = 0


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _mean_vec(vecs: list[np.ndarray]) -> np.ndarray:
    m = np.stack(vecs).mean(axis=0).astype(np.float32)
    n = np.linalg.norm(m)
    return m / n if n > 0 else m


async def run_weekly_recluster(
    session: AsyncSession,
    *,
    window_days: int = 30,
    merge_threshold: float = 0.88,
    split_silhouette_threshold: float = 0.3,
    min_cluster_size: int = 3,
) -> ReclusterReport:
    """Re-cluster the rolling window, merging drifted candidates and splitting overbroad ones."""
    start = time.monotonic()
    report = ReclusterReport()

    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    # Load pain points per embedding_model separately (cross-dim matching is invalid)
    all_pps = (
        await session.execute(
            select(PainPoint)
            .where(PainPoint.embedding.is_not(None))
            .where(PainPoint.extracted_at >= cutoff)
        )
    ).scalars().all()

    pps_by_model: dict[str | None, list[PainPoint]] = defaultdict(list)
    for pp in all_pps:
        pps_by_model[pp.embedding_model].append(pp)

    for emb_model, pps in pps_by_model.items():
        if len(pps) < min_cluster_size:
            continue
        report.embedding_models_processed.append(emb_model or "none")
        await _recluster_for_model(
            session, pps, emb_model,
            merge_threshold=merge_threshold,
            split_silhouette_threshold=split_silhouette_threshold,
            min_cluster_size=min_cluster_size,
            report=report,
        )

    await session.commit()
    report.duration_ms = int((time.monotonic() - start) * 1000)
    log.info(
        "weekly_recluster_complete",
        merged=report.merged_count,
        split=report.split_count,
        relabelled=report.relabelled_count,
    )
    return report


async def _recluster_for_model(
    session: AsyncSession,
    pps: list[PainPoint],
    emb_model: str | None,
    *,
    merge_threshold: float,
    split_silhouette_threshold: float,
    min_cluster_size: int,
    report: ReclusterReport,
) -> None:
    matrix = np.asarray([pp.embedding for pp in pps], dtype=np.float32)
    labels = _cluster_labels(matrix, min_cluster_size)

    # Map: new_cluster_label → list of PainPoint
    new_clusters: dict[int, list[PainPoint]] = defaultdict(list)
    for pp, lbl in zip(pps, labels):
        if lbl != -1:
            new_clusters[lbl].append(pp)

    # Load active candidates for this embedding_model
    candidates = (
        await session.execute(
            select(OpportunityCandidate)
            .where(OpportunityCandidate.is_archived.is_(False))
            .where(OpportunityCandidate.centroid.is_not(None))
            .where(OpportunityCandidate.embedding_model == emb_model)
        )
    ).scalars().all()

    if not candidates:
        return

    candidate_map = {c.id: c for c in candidates}
    candidate_pps: dict[int, list[PainPoint]] = defaultdict(list)
    for pp in pps:
        if pp.candidate_id is not None:
            candidate_pps[pp.candidate_id].append(pp)

    # --- Merge pass: compare all pairs of candidate centroids directly ---
    cands_list = list(candidates)
    merged_ids: set[int] = set()
    for i in range(len(cands_list)):
        ci = cands_list[i]
        if ci.id in merged_ids or ci.centroid is None:
            continue
        for j in range(i + 1, len(cands_list)):
            cj = cands_list[j]
            if cj.id in merged_ids or cj.centroid is None:
                continue
            sim = _cosine_sim(
                np.asarray(ci.centroid, dtype=np.float32),
                np.asarray(cj.centroid, dtype=np.float32),
            )
            if sim < merge_threshold:
                continue
            # Merge: archive the candidate with fewer pain-points
            ci_score = len(candidate_pps.get(ci.id, []))
            cj_score = len(candidate_pps.get(cj.id, []))
            survivor, loser = (ci, cj) if ci_score >= cj_score else (cj, ci)
            loser.is_archived = True
            loser.merged_into_id = survivor.id
            merged_ids.add(loser.id)
            for pp in candidate_pps.get(loser.id, []):
                pp.candidate_id = survivor.id
                candidate_pps[survivor.id].append(pp)
            report.merged_count += 1
            log.info(
                "recluster_merge",
                archived_id=loser.id,
                survivor_id=survivor.id,
                sim=round(sim, 4),
                emb_model=emb_model,
            )
            # Recompute survivor centroid with absorbed pain-points
            all_survivor_pps = candidate_pps.get(survivor.id, [])
            if all_survivor_pps:
                survivor.centroid = _mean_vec(
                    [np.asarray(pp.embedding, dtype=np.float32) for pp in all_survivor_pps]
                ).tolist()
                survivor.last_evidence_at = datetime.now(UTC)

    # --- Split detection ---
    # For each existing candidate, check if its pain-points split across multiple new clusters
    for cand in candidates:
        if cand.is_archived:
            continue
        cand_pp_list = candidate_pps.get(cand.id, [])
        if len(cand_pp_list) < min_cluster_size * 2:
            continue

        # Which new cluster labels appear among this candidate's pain-points?
        pp_to_label = {pp.id: lbl for pp, lbl in zip(pps, labels) if pp.candidate_id == cand.id}
        label_counts: Counter = Counter(pp_to_label.values())
        non_noise = {lbl: cnt for lbl, cnt in label_counts.items() if lbl != -1}

        if len(non_noise) < 2:
            continue

        # Check silhouette-like intra-cluster cohesion (simplified: mean pairwise cosine within cluster)
        vecs = np.asarray([pp.embedding for pp in cand_pp_list], dtype=np.float32)
        centroid = _mean_vec(list(vecs))
        sims = [_cosine_sim(v, centroid) for v in vecs]
        cohesion = float(np.mean(sims))

        if cohesion >= split_silhouette_threshold:
            continue  # cluster is still coherent enough

        # Split: keep largest sub-cluster on original candidate, spawn new ones
        largest_lbl = max(non_noise, key=lambda l: non_noise[l])
        spawned = 0
        for sub_lbl, sub_pps_ids in _group_by_label(pp_to_label).items():
            if sub_lbl == largest_lbl or sub_lbl == -1:
                continue
            sub_pps = [pp for pp in cand_pp_list if pp.id in sub_pps_ids]
            if not sub_pps:
                continue
            new_cand = OpportunityCandidate(
                problem_statement="[unlabelled]",
                centroid=_mean_vec(
                    [np.asarray(pp.embedding, dtype=np.float32) for pp in sub_pps]
                ).tolist(),
                embedding_model=emb_model,
                specificity=0,
                lifecycle_state=None,
                labeller_model=None,
                created_at=datetime.now(UTC),
            )
            session.add(new_cand)
            await session.flush()
            for pp in sub_pps:
                pp.candidate_id = new_cand.id
            spawned += 1

        if spawned:
            # Reassign remaining (largest sub-cluster) to original candidate
            largest_pps = [
                pp for pp in cand_pp_list
                if pp_to_label.get(pp.id) == largest_lbl
            ]
            if largest_pps:
                cand.centroid = _mean_vec(
                    [np.asarray(pp.embedding, dtype=np.float32) for pp in largest_pps]
                ).tolist()
                cand.last_evidence_at = datetime.now(UTC)
            cand.problem_statement = "[unlabelled]"
            cand.labeller_model = None
            report.split_count += 1
            report.relabelled_count += 1
            log.info(
                "recluster_split",
                candidate_id=cand.id,
                sub_clusters=spawned,
                emb_model=emb_model,
            )

    # Re-trigger labelling for candidates with significant evidence-set turnover (>30%)
    await _check_relabel_needed(session, candidates, candidate_pps, report)


async def _check_relabel_needed(
    session: AsyncSession,
    candidates: list[OpportunityCandidate],
    candidate_pps: dict[int, list[PainPoint]],
    report: ReclusterReport,
) -> None:
    for cand in candidates:
        if cand.is_archived or cand.problem_statement == "[unlabelled]":
            continue
        current_pp_ids = {pp.id for pp in candidate_pps.get(cand.id, [])}
        if not current_pp_ids:
            continue
        # Estimate turnover: if candidate has no labeller, it was never labelled
        if cand.labeller_model is None:
            continue
        # We don't have the original set, but if >30% of current PPs were extracted
        # after last labelling, mark for re-label (heuristic)
        last_labelled_at = cand.last_labelled_at
        if last_labelled_at is None:
            continue
        # SQLite returns naive datetimes; normalize to naive for comparison
        llt_naive = last_labelled_at.replace(tzinfo=None)
        recent_count = sum(
            1 for pp in candidate_pps.get(cand.id, [])
            if pp.extracted_at and pp.extracted_at.replace(tzinfo=None) > llt_naive
        )
        if len(current_pp_ids) > 0 and recent_count / len(current_pp_ids) > 0.3:
            cand.problem_statement = "[unlabelled]"
            cand.labeller_model = None
            report.relabelled_count += 1


def _group_by_label(pp_to_label: dict[int, int]) -> dict[int, set[int]]:
    groups: dict[int, set[int]] = defaultdict(set)
    for pp_id, lbl in pp_to_label.items():
        groups[lbl].add(pp_id)
    return dict(groups)
