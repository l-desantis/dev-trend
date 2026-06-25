"""Composite candidate scorer — writes CandidateScoreHistory rows."""
from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import CandidateScoreHistory, OpportunityCandidate
from app.scoring.dimensions import (
    frequency_raw,
    get_latest_validation,
    momentum_raw,
    source_diversity_raw,
    specificity_raw,
    validation_curve,
)
from app.scoring.normalize import normalize_with_neutral_fallback

log = structlog.get_logger(__name__)

WEIGHTS: dict[str, float] = {
    "frequency": 0.25,
    "momentum": 0.30,
    "source_diversity": 0.15,
    "validation": 0.20,
    "specificity": 0.10,
}


async def score_all_candidates(
    session: AsyncSession,
    *,
    as_of: datetime,
    gate: int | None = None,
) -> list[CandidateScoreHistory]:
    """Score all active above-gate candidates and persist CandidateScoreHistory.

    Idempotent for the same as_of date: existing rows for that date are deleted first.
    """
    if gate is None:
        gate = get_settings().specificity_gate

    # Fetch active, above-gate candidates
    result = await session.execute(
        select(OpportunityCandidate)
        .where(OpportunityCandidate.is_archived.is_(False))
        .where(OpportunityCandidate.specificity > gate)
    )
    candidates = result.scalars().all()

    if not candidates:
        return []

    # Idempotency: delete existing rows for this as_of date (single batch)
    as_of_start = datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC)
    as_of_end = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=UTC)
    candidate_ids = [c.id for c in candidates]
    await session.execute(
        delete(CandidateScoreHistory)
        .where(CandidateScoreHistory.candidate_id.in_(candidate_ids))
        .where(CandidateScoreHistory.scored_at >= as_of_start)
        .where(CandidateScoreHistory.scored_at <= as_of_end)
    )

    # Compute raw values for normalised dimensions
    freq_raws: dict[int, float] = {}
    mom_raws: dict[int, float] = {}
    div_raws: dict[int, float] = {}

    for c in candidates:
        freq_raws[c.id] = await frequency_raw(session, c.id)
        mom_raws[c.id] = await momentum_raw(session, c.id)
        div_raws[c.id] = await source_diversity_raw(session, c.id)

    # Percentile normalise (with neutral fallback for small populations)
    freq_scores = normalize_with_neutral_fallback(freq_raws)
    mom_scores = normalize_with_neutral_fallback(mom_raws)
    div_scores = normalize_with_neutral_fallback(div_raws)

    inserted: list[CandidateScoreHistory] = []

    for c in candidates:
        val_row = await get_latest_validation(session, c.id)
        if val_row and val_row.metadata_json:
            repo_count = int(val_row.metadata_json.get("repo_count", 0))
            max_stars = int(val_row.metadata_json.get("max_stars", 0))
        else:
            repo_count, max_stars = 0, 0

        val_score = validation_curve(repo_count, max_stars)  # may be None
        spec_score = specificity_raw(c)

        breakdown = {
            "frequency": {"raw": freq_raws[c.id], "score": freq_scores[c.id]},
            "momentum": {"raw": mom_raws[c.id], "score": mom_scores[c.id]},
            "source_diversity": {"raw": div_raws[c.id], "score": div_scores[c.id]},
            "validation": val_score,
            "specificity": spec_score,
            "weights": WEIGHTS,
        }

        weighted = (
            freq_scores[c.id] * WEIGHTS["frequency"]
            + mom_scores[c.id] * WEIGHTS["momentum"]
            + div_scores[c.id] * WEIGHTS["source_diversity"]
            + spec_score * WEIGHTS["specificity"]
        )
        if val_score is None:
            # No GitHub signal: drop the validation dimension and rescale the
            # remaining four weights to sum to 1.0, so the candidate is neither
            # rewarded nor penalised on that axis.
            remaining = 1.0 - WEIGHTS["validation"]
            total = weighted / remaining if remaining > 0 else 0.0
        else:
            total = weighted + val_score * WEIGHTS["validation"]

        row = CandidateScoreHistory(
            candidate_id=c.id,
            score_total=total,
            score_breakdown_json=breakdown,
            scored_at=as_of,
        )
        session.add(row)
        inserted.append(row)

    await session.commit()
    log.info("scoring_complete", candidates=len(inserted), as_of=as_of.isoformat())
    return inserted
