"""Import verified portal readings into Home Assistant long-term statistics."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta, tzinfo

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .calculation import total_cost
from .const import DOMAIN
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
    initial_sum: float = 0.0,
    initial_meter_state: float | None = None,
) -> list[StatisticData]:
    """Convert completed portal hours to total-increasing external statistics."""
    current_hour_utc = dt_util.as_utc(now).replace(minute=0, second=0, microsecond=0)
    result: dict[datetime, StatisticData] = {}
    previous_state = initial_meter_state
    running_sum = initial_sum
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
        if previous_state is not None:
            # A physical register can reset after replacement. The first
            # reading of the new register is a new baseline, not consumption
            # that occurred during this single hour.
            delta = reading.meter_state_m3 - previous_state
            if delta >= 0:
                running_sum += delta
        previous_state = reading.meter_state_m3
        result[start] = StatisticData(
            start=start,
            state=reading.meter_state_m3,
            sum=round(running_sum, 6),
        )
    return [result[start] for start in sorted(result)]


def cost_statistics(
    readings: Iterable[MeterReading],
    *,
    price_per_m3: float,
    local_tz: tzinfo,
    now: datetime,
    initial_sum: float = 0.0,
    initial_meter_state: float | None = None,
) -> list[StatisticData]:
    """Convert completed readings to idempotent cumulative cost statistics."""
    result: list[StatisticData] = []
    previous_sum = initial_sum
    for row in meter_statistics(
        readings,
        local_tz=local_tz,
        now=now,
        initial_sum=initial_sum,
        initial_meter_state=initial_meter_state,
    ):
        cumulative_cost = total_cost(row["sum"], price_per_m3)
        result.append(
            StatisticData(
                start=row["start"],
                state=total_cost(row["sum"] - previous_sum, price_per_m3),
                sum=cumulative_cost,
            )
        )
        previous_sum = row["sum"]
    return result


@callback
def async_add_external_meter_statistics(
    hass: HomeAssistant,
    *,
    statistic_id: str,
    readings: Iterable[MeterReading],
    local_tz: tzinfo,
    now: datetime,
    initial_sum: float = 0.0,
    initial_meter_state: float | None = None,
) -> int:
    """Queue portal readings under the integration-owned external ID."""
    statistics = meter_statistics(
        readings,
        local_tz=local_tz,
        now=now,
        initial_sum=initial_sum,
        initial_meter_state=initial_meter_state,
    )
    if not statistics:
        return 0
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name="Water consumption",
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=VolumeConverter.UNIT_CLASS,
        unit_of_measurement=UnitOfVolume.CUBIC_METERS,
    )
    async_add_external_statistics(hass, metadata, statistics)
    return len(statistics)


@callback
def async_add_external_cost_statistics(
    hass: HomeAssistant,
    *,
    statistic_id: str,
    readings: Iterable[MeterReading],
    price_per_m3: float,
    currency: str,
    local_tz: tzinfo,
    now: datetime,
    initial_sum: float = 0.0,
    initial_meter_state: float | None = None,
) -> int:
    """Queue matching cumulative costs under the integration-owned ID."""
    statistics = cost_statistics(
        readings,
        price_per_m3=price_per_m3,
        local_tz=local_tz,
        now=now,
        initial_sum=initial_sum,
        initial_meter_state=initial_meter_state,
    )
    if not statistics:
        return 0
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name="Water cost",
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=None,
        unit_of_measurement=currency,
    )
    async_add_external_statistics(hass, metadata, statistics)
    return len(statistics)
