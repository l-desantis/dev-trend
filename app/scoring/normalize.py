"""Percentile normalisation across the candidate population."""
from __future__ import annotations

from app.features.trend_features import percentile_rank


def normalize_dimension_across_candidates(
    raw_values: dict[int, float],
) -> dict[int, float]:
    """Returns candidate_id → percentile-rank score in [0, 100]."""
    all_raws = list(raw_values.values())
    return {
        cid: percentile_rank(all_raws, raw)
        for cid, raw in raw_values.items()
    }


def normalize_with_neutral_fallback(
    raw_values: dict[int, float],
    *,
    min_population: int = 5,
    fallback: float = 50.0,
) -> dict[int, float]:
    """If population < min_population, return fallback for every candidate."""
    if len(raw_values) < min_population:
        return {cid: fallback for cid in raw_values}
    return normalize_dimension_across_candidates(raw_values)
