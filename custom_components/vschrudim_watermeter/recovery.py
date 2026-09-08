"""Pure helpers for detecting and recovering incomplete hourly series."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from .models import MeterReading


def merge_readings(*groups: Iterable[MeterReading]) -> tuple[MeterReading, ...]:
    """Merge repeated downloads, preferring the newest value for a timestamp."""
    merged: dict[datetime, MeterReading] = {}
    for group in groups:
        for reading in group:
            merged[reading.timestamp] = reading
    return tuple(sorted(merged.values(), key=lambda item: item.timestamp))


def count_duplicate_readings(*groups: Iterable[MeterReading]) -> int:
    """Return the number of timestamp collisions that a merge will collapse."""
    seen: set[datetime] = set()
    duplicates = 0
    for group in groups:
        for reading in group:
            if reading.timestamp in seen:
                duplicates += 1
            else:
                seen.add(reading.timestamp)
    return duplicates


def find_missing_hours(readings: Iterable[MeterReading]) -> tuple[datetime, ...]:
    """Return gaps between confirmed hourly readings.

    The nonexistent 02:00 hour on the Czech spring DST transition is excluded.
    """
    timestamps = sorted({item.timestamp.replace(minute=0, second=0, microsecond=0) for item in readings})
    if len(timestamps) < 2:
        return ()
    present = set(timestamps)
    missing: list[datetime] = []
    candidate = timestamps[0] + timedelta(hours=1)
    while candidate < timestamps[-1]:
        if candidate not in present and not _is_czech_spring_dst_gap(candidate):
            missing.append(candidate)
        candidate += timedelta(hours=1)
    return tuple(missing)


def _is_czech_spring_dst_gap(value: datetime) -> bool:
    """Return whether a naive local time is the nonexistent Czech DST hour."""
    if value.month != 3 or value.hour != 2 or value.weekday() != 6:
        return False
    return (value + timedelta(days=7)).month == 4
