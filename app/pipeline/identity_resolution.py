"""Stage 3 — identity resolution: attach PainPoints to existing candidates."""
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OpportunityCandidate, PainPoint
from app.pipeline.embedding_index import EmbeddingIndex

log = structlog.get_logger(__name__)


@dataclass
class IdentityResolutionReport:
    unattached_checked: int = 0
    attached: int = 0
    duration_ms: int = 0


async def run_identity_resolution(
    session: AsyncSession,
    *,
    threshold: float,
) -> IdentityResolutionReport:
    """Stage 3: attach unmatched PainPoints to the nearest active candidate."""
    start = time.monotonic()
    report = IdentityResolutionReport()

    # Load active candidates with a centroid
    candidates = (
        await session.execute(
            select(OpportunityCandidate)
            .where(OpportunityCandidate.is_archived.is_(False))
            .where(OpportunityCandidate.centroid.is_not(None))
        )
    ).scalars().all()

    if not candidates:
        report.duration_ms = int((time.monotonic() - start) * 1000)
        return report

    index = EmbeddingIndex(
        ids=[c.id for c in candidates],
        vectors=[c.centroid for c in candidates],
    )
    candidate_map = {c.id: c for c in candidates}

    # Load unattached PainPoints with embeddings
    unattached = (
        await session.execute(
            select(PainPoint)
            .where(PainPoint.candidate_id.is_(None))
            .where(PainPoint.embedding.is_not(None))
        )
    ).scalars().all()

    # Track which candidates need centroid updates
    updated_candidates: set[int] = set()

    for pp in unattached:
        report.unattached_checked += 1
        results = index.nearest(pp.embedding, k=1, threshold=threshold)
        if not results:
            continue
        cid, _sim = results[0]
        pp.candidate_id = cid
        report.attached += 1
        updated_candidates.add(cid)

    await session.flush()

    # Recompute centroids for affected candidates
    for cid in updated_candidates:
        candidate = candidate_map[cid]
        all_pps = (
            await session.execute(
                select(PainPoint)
                .where(PainPoint.candidate_id == cid)
                .where(PainPoint.embedding.is_not(None))
            )
        ).scalars().all()
        if not all_pps:
            continue
        matrix = np.asarray([pp.embedding for pp in all_pps], dtype=np.float32)
        mean_vec = matrix.mean(axis=0)
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm
        candidate.centroid = mean_vec.tolist()
        candidate.last_evidence_at = datetime.now(UTC)

    await session.commit()
    report.duration_ms = int((time.monotonic() - start) * 1000)
    log.info("identity_resolution_complete", **{k: v for k, v in report.__dict__.items()})
    return report
