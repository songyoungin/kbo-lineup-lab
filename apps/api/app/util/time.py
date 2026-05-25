"""Shared datetime utilities."""

from datetime import UTC, datetime


def to_utc(dt: datetime) -> datetime:
    """Normalize a tz-aware datetime to UTC.

    Args:
        dt: tz-aware datetime. Raises ValueError for naive datetimes.

    Returns:
        Equivalent datetime in UTC.

    Raises:
        ValueError: If dt has no tzinfo (naive datetime).
    """
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt.astimezone(UTC)
