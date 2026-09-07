"""Pure calculations for water-meter readings."""
from collections.abc import Iterable
from .models import MeterReading

def consumption_deltas(readings: Iterable[MeterReading]) -> list[tuple[MeterReading, float | None]]:
    """Return consumption as the non-negative delta to the preceding meter state."""
    ordered = sorted(readings, key=lambda item: item.timestamp)
    previous: float | None = None
    result = []
    for reading in ordered:
        # The portal values are decimal quantities; keep binary float artefacts
        # out of entity state and long-term statistics.
        delta = None if previous is None else round(reading.meter_state_m3 - previous, 6)
        result.append((reading, delta if delta is not None and delta >= 0 else None))
        previous = reading.meter_state_m3
    return result

def latest_consumption(readings: Iterable[MeterReading]) -> float | None:
    """Return the latest valid incremental consumption in m³."""
    values = consumption_deltas(readings)
    return values[-1][1] if values else None


def total_cost(meter_state_m3: float, price_per_m3: float) -> float:
    """Return a stable cumulative cost matching the cumulative meter state."""
    return round(meter_state_m3 * price_per_m3, 6)
