"""Pipeline orchestrator — runs stages 1–5 in order."""
import time
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.llm.base import LLMAdapter
from app.llm.embedding_base import EmbeddingAdapter
from app.pipeline.clustering import ClusteringReport, run_clustering
from app.pipeline.embed import EmbeddingReport, run_embedding
from app.pipeline.extract import ExtractionReport, run_extraction
from app.pipeline.identity_resolution import IdentityResolutionReport, run_identity_resolution
from app.pipeline.labelling import LabellingReport, run_labelling

log = structlog.get_logger(__name__)


@dataclass
class PipelineReport:
    extraction: ExtractionReport | None = None
    embedding: EmbeddingReport | None = None
    identity_resolution: IdentityResolutionReport | None = None
    clustering: ClusteringReport | None = None
    labelling: LabellingReport | None = None
    total_ms: int = 0


async def run_pipeline(
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMAdapter,
    embedder: EmbeddingAdapter,
    settings: Settings,
    *,
    since: datetime | None = None,
) -> PipelineReport:
    """Run all 5 pipeline stages sequentially. Each stage uses its own session."""
    report = PipelineReport()
    wall_start = time.monotonic()

    log.info("pipeline_start", since=since.isoformat() if since else None)

    async with session_factory() as session:
        report.extraction = await run_extraction(
            session, llm,
            since=since,
            batch_size=settings.extraction_batch_size,
        )

    async with session_factory() as session:
        report.embedding = await run_embedding(
            session, embedder,
            batch_size=settings.embedding_batch_size,
        )

    async with session_factory() as session:
        report.identity_resolution = await run_identity_resolution(
            session,
            threshold=settings.identity_resolution_threshold,
        )

    async with session_factory() as session:
        report.clustering = await run_clustering(
            session,
            min_cluster_size=settings.clustering_min_cluster_size,
        )

    async with session_factory() as session:
        report.labelling = await run_labelling(session, llm)

    report.total_ms = int((time.monotonic() - wall_start) * 1000)
    log.info(
        "pipeline_complete",
        total_ms=report.total_ms,
        painpoints_created=report.extraction.painpoints_created if report.extraction else 0,
        candidates_created=report.clustering.candidates_created if report.clustering else 0,
        labelled=report.labelling.labelled if report.labelling else 0,
    )
    return report
