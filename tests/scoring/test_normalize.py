"""Tests for app/scoring/normalize.py"""
from app.scoring.normalize import (
    normalize_dimension_across_candidates,
    normalize_with_neutral_fallback,
)


def test_normalize_basic() -> None:
    raws = {1: 10.0, 2: 30.0, 3: 50.0, 4: 70.0, 5: 90.0}
    result = normalize_dimension_across_candidates(raws)
    assert result[1] < result[2] < result[3] < result[4] < result[5]
    for v in result.values():
        assert 0.0 <= v <= 100.0


def test_normalize_neutral_fallback_applies() -> None:
    raws = {1: 10.0, 2: 20.0, 3: 30.0}
    result = normalize_with_neutral_fallback(raws, min_population=5)
    assert all(v == 50.0 for v in result.values())


def test_normalize_handles_ties() -> None:
    raws = {1: 10.0, 2: 10.0, 3: 10.0, 4: 10.0, 5: 10.0}
    result = normalize_dimension_across_candidates(raws)
    # All tied → all get same rank (50th percentile: 0 below + 5*0.5/5 * 100 = 50)
    values = list(result.values())
    assert all(v == values[0] for v in values)
