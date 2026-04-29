"""Stage 4 — cluster unmatched PainPoints into new OpportunityCandidates."""
import time
from dataclasses import dataclass

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OpportunityCandidate, PainPoint

log = structlog.get_logger(__name__)


@dataclass
class ClusteringReport:
    unattached: int = 0
    candidates_created: int = 0
    noise_points: int = 0
    duration_ms: int = 0


def _cluster_labels(matrix: np.ndarray, min_cluster_size: int) -> np.ndarray:
    """Return cluster label array (-1 = noise). Tries HDBSCAN, falls back to Agglomerative.

    hdbscan is intentionally omitted from pyproject.toml: the runtime import-and-fallback
    is safer on WSL2 where the native binary may be absent.
    """
    try:
        import hdbscan
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            metric="euclidean",
        )
        return clusterer.fit_predict(matrix)
    except ImportError:
        pass

    from sklearn.cluster import AgglomerativeClustering
    n = len(matrix)
    if n < min_cluster_size:
        return np.full(n, -1, dtype=int)
    n_clusters = max(1, n // min_cluster_size)
    labels = AgglomerativeClustering(
        n_clusters=n_clusters, metric="cosine", linkage="average"
    ).fit_predict(matrix)
    # Mark clusters smaller than min_cluster_size as noise
    from collections import Counter
    counts = Counter(labels)
    return np.array([
        lbl if counts[lbl] >= min_cluster_size else -1
        for lbl in labels
    ], dtype=int)


async def run_clustering(
    session: AsyncSession,
    *,
    min_cluster_size: int,
) -> ClusteringReport:
    """Stage 4: cluster unattached PainPoints and create new OpportunityCandidates."""
    start = time.monotonic()
    report = ClusteringReport()

    unattached = (
        await session.execute(
            select(PainPoint)
            .where(PainPoint.candidate_id.is_(None))
            .where(PainPoint.embedding.is_not(None))
        )
    ).scalars().all()

    report.unattached = len(unattached)

    if len(unattached) < min_cluster_size:
        report.duration_ms = int((time.monotonic() - start) * 1000)
        return report

    matrix = np.asarray([pp.embedding for pp in unattached], dtype=np.float32)
    labels = _cluster_labels(matrix, min_cluster_size)

    unique_labels = set(labels) - {-1}
    report.noise_points = int((labels == -1).sum())

    for lbl in unique_labels:
        idxs = np.where(labels == lbl)[0]
        cluster_pps = [unattached[i] for i in idxs]

        cluster_vecs = matrix[idxs]
        mean_vec = cluster_vecs.mean(axis=0)
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm

        candidate = OpportunityCandidate(
            problem_statement="",
            centroid=mean_vec.tolist(),
            specificity=0,
            lifecycle_state=None,
            labeller_model=None,  # unlabelled sentinel
        )
        session.add(candidate)
        await session.flush()

        for pp in cluster_pps:
            pp.candidate_id = candidate.id

        report.candidates_created += 1

    await session.commit()
    report.duration_ms = int((time.monotonic() - start) * 1000)
    log.info("clustering_complete", **{k: v for k, v in report.__dict__.items()})
    return report
