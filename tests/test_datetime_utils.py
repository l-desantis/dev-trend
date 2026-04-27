from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.utils.datetime_utils import utc_day_bounds, utc_now, utc_start_of_day


def test_utc_now_is_aware():
    result = utc_now()
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


@pytest.mark.parametrize("dt,expected_date", [
    (datetime(2024, 6, 15, 23, 59, 59, tzinfo=UTC), (2024, 6, 15)),
    (datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC), (2024, 6, 15)),
])
def test_utc_start_of_day_aware(dt, expected_date):
    result = utc_start_of_day(dt)
    assert (result.year, result.month, result.day) == expected_date
    assert result.hour == result.minute == result.second == result.microsecond == 0
    assert result.tzinfo is not None


def test_utc_start_of_day_naive_treated_as_utc():
    naive = datetime(2024, 3, 10, 14, 30, 0)
    result = utc_start_of_day(naive)
    assert result == datetime(2024, 3, 10, 0, 0, 0, tzinfo=UTC)


def test_utc_start_of_day_non_utc_tz_converts():
    plus5 = timezone(timedelta(hours=5))
    # 2024-06-16 01:00 +05:00 == 2024-06-15 20:00 UTC → day start should be 2024-06-15 UTC
    dt = datetime(2024, 6, 16, 1, 0, 0, tzinfo=plus5)
    result = utc_start_of_day(dt)
    assert result == datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC)


def test_utc_day_bounds_is_24_hours():
    dt = datetime(2024, 1, 20, 12, 0, tzinfo=UTC)
    start, end = utc_day_bounds(dt)
    assert end - start == timedelta(days=1)


def test_utc_day_bounds_start_is_midnight():
    dt = datetime(2024, 1, 20, 12, 0, tzinfo=UTC)
    start, end = utc_day_bounds(dt)
    assert start == datetime(2024, 1, 20, 0, 0, tzinfo=UTC)
    assert end == datetime(2024, 1, 21, 0, 0, tzinfo=UTC)
