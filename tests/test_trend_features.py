from app.features.trend_features import percentile_rank, rolling_slope


def test_rolling_slope_constant_series_is_zero():
    assert rolling_slope([5, 5, 5, 5, 5, 5, 5]) == 0.0


def test_rolling_slope_rising_series_is_positive():
    assert rolling_slope([1, 2, 3, 4, 5, 6, 7]) == 1.0


def test_rolling_slope_declining_series_is_negative():
    assert rolling_slope([7, 6, 5, 4, 3, 2, 1]) == -1.0


def test_rolling_slope_returns_zero_for_fewer_than_two_points():
    assert rolling_slope([]) == 0.0
    assert rolling_slope([42]) == 0.0


def test_percentile_rank_target_above_all_is_100():
    assert percentile_rank([1, 2, 3, 4, 5], 10.0) == 100.0


def test_percentile_rank_target_below_all_is_zero():
    assert percentile_rank([1, 2, 3, 4, 5], 0.0) == 0.0


def test_percentile_rank_midpoint_is_50():
    # Value 3 has two below, two above, one equal → (2 + 0.5*1)/5 = 0.5 → 50
    assert percentile_rank([1, 2, 3, 4, 5], 3.0) == 50.0


def test_percentile_rank_insufficient_history_returns_neutral_50():
    assert percentile_rank([], 42.0) == 50.0
    assert percentile_rank([7.0], 42.0) == 50.0
