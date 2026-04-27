"""Pure numerical helpers for trend scoring. No I/O, no DB."""


def rolling_slope(values: list[float]) -> float:
    """Ordinary least squares slope over evenly spaced x = 0, 1, 2, ...

    Returns 0.0 when fewer than two points are available.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = 0.0
    den = 0.0
    for i, y in enumerate(values):
        dx = i - mean_x
        num += dx * (y - mean_y)
        den += dx * dx
    if den == 0:
        return 0.0
    return num / den


def percentile_rank(history: list[float], target: float) -> float:
    """Return target's percentile rank (0..100) against history.

    Uses the standard definition: (#below + 0.5 * #equal) / n * 100.
    Returns 50.0 as a neutral fallback when history has fewer than 2 values;
    early-life niches get a middle-of-pack score until enough history accrues.
    """
    if len(history) < 2:
        return 50.0
    below = sum(1 for v in history if v < target)
    equal = sum(1 for v in history if v == target)
    return ((below + 0.5 * equal) / len(history)) * 100.0
