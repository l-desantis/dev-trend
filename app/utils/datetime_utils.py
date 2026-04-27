"""UTC datetime helpers shared across agents, features, and forecasting."""
from datetime import UTC, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_start_of_day(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def utc_day_bounds(as_of: datetime) -> tuple[datetime, datetime]:
    day_start = utc_start_of_day(as_of)
    return day_start, day_start + timedelta(days=1)
