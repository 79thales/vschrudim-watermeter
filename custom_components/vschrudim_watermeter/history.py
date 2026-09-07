"""Import verified portal readings into Home Assistant long-term statistics."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta, tzinfo

from homeassistant.components.recorder.const import DOMAIN as RECORDER_DOMAIN
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .models import MeterReading


def history_ranges_backwards(
    date_from: date,
    date_to: date,
    *,
    days_per_request: int = 31,
) -> tuple[tuple[date, date], ...]:
    """Split an inclusive date interval into newest-first portal requests."""
    if date_to < date_from:
        return ()
    ranges: list[tuple[date, date]] = []
    cursor = date_to
    while cursor >= date_from:
        chunk_from = max(date_from, cursor - timedelta(days=days_per_request - 1))
        ranges.append((chunk_from, cursor))
        cursor = chunk_from - timedelta(days=1)
    return tuple(ranges)


def meter_statistics(
    readings: Iterable[MeterReading],
    *,
    local_tz: tzinfo,
    now: datetime,
) -> list[StatisticData]:
    """Convert completed portal hours to idempotent cumulative statistics."""
    current_hour_utc = dt_util.as_utc(now).replace(minute=0, second=0, microsecond=0)
    result: dict[datetime, StatisticData] = {}
    for reading in sorted(readings, key=lambda item: item.timestamp):
        local_start = reading.timestamp.replace(
            minute=0,
            second=0,
            microsecond=0,
            tzinfo=local_tz,
        )
        start = dt_util.as_utc(local_start)
        if start >= current_hour_utc:
            continue
        result[start] = StatisticData(
            start=start,
            state=reading.meter_state_m3,
            # The source is a physical cumulative register. Using its absolute
            # state keeps independently downloaded/resumed ranges consistent.
            sum=reading.meter_state_m3,
        )
    return [result[start] for start in sorted(result)]


@callback
def async_import_meter_history(
    hass: HomeAssistant,
    *,
    entity_id: str,
    readings: Iterable[MeterReading],
    local_tz: tzinfo,
    now: datetime,
) -> int:
    """Queue history under the real sensor statistic ID used by Energy."""
    statistics = meter_statistics(readings, local_tz=local_tz, now=now)
    if not statistics:
        return 0
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=None,
        source=RECORDER_DOMAIN,
        statistic_id=entity_id,
        unit_class=VolumeConverter.UNIT_CLASS,
        unit_of_measurement=UnitOfVolume.CUBIC_METERS,
    )
    async_import_statistics(hass, metadata, statistics)
    return len(statistics)
