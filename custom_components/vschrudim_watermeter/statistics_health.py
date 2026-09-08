"""Privacy-safe, read-only health helpers for Energy statistics.

The helpers in this module deliberately know nothing about Home Assistant's
Recorder API.  They operate on the small, already-redacted set of statistic
fields returned by Recorder and make the coordinator's health decisions easy
to test without a running Home Assistant instance.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re

from .attempts import sanitize_error_message
from .models import MeterReading


STATISTICS_WRITE_STATUSES = frozenset({"never", "ok", "pending", "error"})
ENERGY_HEALTH_STATUSES = frozenset(
    {"unknown", "ok", "pending", "incomplete", "error"}
)
METER_REGISTER_STATUSES = frozenset({"normal", "possible_reset_or_correction"})


def _as_utc(value: object) -> datetime | None:
    """Return a timestamp in UTC from a Recorder statistic start value."""
    try:
        if isinstance(value, datetime):
            timestamp = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                return None
            timestamp = datetime.fromtimestamp(float(value), timezone.utc)
        elif isinstance(value, str):
            timestamp = datetime.fromisoformat(value)
        else:
            return None
    except (OverflowError, OSError, TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        # Recorder numeric starts are UTC.  Treat a defensive naïve value the
        # same way rather than applying the configured local timezone twice.
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _finite_number(value: object) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rows_starts(rows: Iterable[Mapping[str, object]]) -> list[datetime]:
    return [
        timestamp
        for row in rows
        if (timestamp := _as_utc(row.get("start"))) is not None
    ]


def _internal_gaps(starts: Iterable[datetime]) -> int:
    """Count missing UTC hour slots between observed statistic starts.

    UTC arithmetic deliberately avoids Czech daylight-saving transitions:
    the spring skipped local hour does not exist as a UTC gap and both autumn
    local 02:00 hours remain distinct UTC instants.
    """
    ordered = sorted(set(starts))
    gaps = 0
    for previous, current in zip(ordered, ordered[1:]):
        seconds = (current - previous).total_seconds()
        if seconds > timedelta(hours=1).total_seconds():
            gaps += max(0, int(seconds // 3600) - 1)
    return gaps


def _sum_is_monotonic(rows: Iterable[Mapping[str, object]]) -> bool | None:
    """Check finite cumulative sums in chronological UTC order."""
    ordered: list[tuple[datetime, float]] = []
    for row in rows:
        start = _as_utc(row.get("start"))
        if start is None or row.get("sum") is None:
            continue
        total = _finite_number(row.get("sum"))
        if total is None:
            return False
        ordered.append((start, total))
    if not ordered:
        return None
    ordered.sort(key=lambda row: row[0])
    return all(current >= previous for (_, previous), (_, current) in zip(ordered, ordered[1:]))


def _statistic_starts(rows: Iterable[Mapping[str, object]]) -> tuple[datetime, ...]:
    """Return unique, sorted UTC starts from any Recorder row sequence."""
    return tuple(sorted(set(_rows_starts(rows))))


def _expected_starts(rows: Iterable[Mapping[str, object]]) -> tuple[datetime, ...]:
    """Normalize completed portal-derived StatisticData starts to UTC."""
    return _statistic_starts(rows)


@dataclass(frozen=True, slots=True)
class EnergyStatisticsHealth:
    """A safe snapshot comparing portal-derived and Recorder statistics."""

    status: str = "unknown"
    checked_at: datetime | None = None
    portal_latest_timestamp: datetime | None = None
    consumption_earliest_timestamp: datetime | None = None
    consumption_latest_timestamp: datetime | None = None
    cost_earliest_timestamp: datetime | None = None
    cost_latest_timestamp: datetime | None = None
    expected_consumption_points: int = 0
    expected_cost_points: int = 0
    consumption_point_count: int = 0
    cost_point_count: int = 0
    missing_consumption_points: int = 0
    missing_cost_points: int = 0
    consumption_internal_gaps: int = 0
    cost_internal_gaps: int = 0
    consumption_duplicate_timestamps: int = 0
    cost_duplicate_timestamps: int = 0
    consumption_sum_monotonic: bool | None = None
    cost_sum_monotonic: bool | None = None
    write_pending: bool = False
    error_type: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Serialize only safe counts, states and timestamps."""
        return {
            "status": self.status,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "portal_latest_timestamp": (
                self.portal_latest_timestamp.isoformat()
                if self.portal_latest_timestamp
                else None
            ),
            "consumption_earliest_timestamp": (
                self.consumption_earliest_timestamp.isoformat()
                if self.consumption_earliest_timestamp
                else None
            ),
            "consumption_latest_timestamp": (
                self.consumption_latest_timestamp.isoformat()
                if self.consumption_latest_timestamp
                else None
            ),
            "cost_earliest_timestamp": (
                self.cost_earliest_timestamp.isoformat()
                if self.cost_earliest_timestamp
                else None
            ),
            "cost_latest_timestamp": (
                self.cost_latest_timestamp.isoformat()
                if self.cost_latest_timestamp
                else None
            ),
            "expected_consumption_points": self.expected_consumption_points,
            "expected_cost_points": self.expected_cost_points,
            "consumption_point_count": self.consumption_point_count,
            "cost_point_count": self.cost_point_count,
            "missing_consumption_points": self.missing_consumption_points,
            "missing_cost_points": self.missing_cost_points,
            "consumption_internal_gaps": self.consumption_internal_gaps,
            "cost_internal_gaps": self.cost_internal_gaps,
            "consumption_duplicate_timestamps": self.consumption_duplicate_timestamps,
            "cost_duplicate_timestamps": self.cost_duplicate_timestamps,
            "consumption_sum_monotonic": self.consumption_sum_monotonic,
            "cost_sum_monotonic": self.cost_sum_monotonic,
            "write_pending": self.write_pending,
            "error_type": self.error_type,
            "error": sanitize_error_message(self.error),
        }


@dataclass(frozen=True, slots=True)
class MeterRegisterHealth:
    """Informational detection of a lower meter register value."""

    status: str = "normal"
    new_decrease_timestamp: datetime | None = None
    decrease_count_current_readings: int = 0
    last_check_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "new_decrease_timestamp": (
                self.new_decrease_timestamp.isoformat()
                if self.new_decrease_timestamp
                else None
            ),
            "decrease_count_current_readings": self.decrease_count_current_readings,
            "last_check_at": self.last_check_at.isoformat()
            if self.last_check_at
            else None,
        }


@dataclass(frozen=True, slots=True)
class StatisticsState:
    """The bounded, privacy-safe state persisted beside backfill progress."""

    status: str = "never"
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_type: str | None = None
    last_error: str | None = None
    last_written_points: int = 0
    recovery_pending: bool = False
    health_status: str = "unknown"
    last_health_check_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "last_attempt_at": self.last_attempt_at.isoformat()
            if self.last_attempt_at
            else None,
            "last_success_at": self.last_success_at.isoformat()
            if self.last_success_at
            else None,
            "last_error_type": self.last_error_type,
            "last_error": sanitize_error_message(self.last_error),
            "last_written_points": self.last_written_points,
            "recovery_pending": self.recovery_pending,
            "health_status": self.health_status,
            "last_health_check_at": self.last_health_check_at.isoformat()
            if self.last_health_check_at
            else None,
        }

    @classmethod
    def from_dict(cls, value: object) -> StatisticsState:
        """Load a partial or malformed state defensively."""
        if not isinstance(value, Mapping):
            return cls()

        def timestamp(key: str) -> datetime | None:
            raw = value.get(key)
            if not isinstance(raw, str):
                return None
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return None

        status = value.get("status")
        health_status = value.get("health_status")
        error_type = value.get("last_error_type")
        try:
            written_points = max(0, int(value.get("last_written_points", 0)))
        except (TypeError, ValueError):
            written_points = 0
        return cls(
            status=status if status in STATISTICS_WRITE_STATUSES else "never",
            last_attempt_at=timestamp("last_attempt_at"),
            last_success_at=timestamp("last_success_at"),
            last_error_type=(
                str(error_type)[:80]
                if isinstance(error_type, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", error_type)
                else None
            ),
            last_error=sanitize_error_message(value.get("last_error")),
            last_written_points=written_points,
            recovery_pending=bool(value.get("recovery_pending")),
            health_status=(
                health_status
                if health_status in ENERGY_HEALTH_STATUSES
                else "unknown"
            ),
            last_health_check_at=timestamp("last_health_check_at"),
        )


@dataclass(frozen=True, slots=True)
class StatisticsImportResult:
    """Internal result of a non-destructive external-statistics write."""

    accepted: bool
    written_points: int = 0
    error_type: str | None = None
    error: str | None = None


def assess_energy_statistics(
    *,
    expected_consumption_rows: Iterable[Mapping[str, object]],
    expected_cost_rows: Iterable[Mapping[str, object]],
    consumption_rows: Iterable[Mapping[str, object]],
    cost_rows: Iterable[Mapping[str, object]],
    portal_latest_timestamp: datetime | None,
    write_pending: bool,
    checked_at: datetime,
) -> EnergyStatisticsHealth:
    """Compare expected completed portal hours with Recorder rows.

    The expected inputs come from the existing external-statistics builders,
    so this check does not invent calendar hours or treat an old/unchanged
    portal endpoint as a source failure.
    """
    expected_consumption = _expected_starts(expected_consumption_rows)
    expected_cost = _expected_starts(expected_cost_rows)
    consumption = list(consumption_rows)
    cost = list(cost_rows)
    consumption_starts_raw = _rows_starts(consumption)
    cost_starts_raw = _rows_starts(cost)
    consumption_starts = tuple(sorted(set(consumption_starts_raw)))
    cost_starts = tuple(sorted(set(cost_starts_raw)))
    missing_consumption = len(set(expected_consumption) - set(consumption_starts))
    missing_cost = len(set(expected_cost) - set(cost_starts))
    consumption_duplicates = len(consumption_starts_raw) - len(consumption_starts)
    cost_duplicates = len(cost_starts_raw) - len(cost_starts)
    consumption_monotonic = _sum_is_monotonic(consumption)
    cost_monotonic = _sum_is_monotonic(cost)

    if write_pending:
        status = "pending"
    elif not expected_consumption and not expected_cost:
        status = "unknown"
    elif (
        missing_consumption
        or missing_cost
        or consumption_duplicates
        or cost_duplicates
        or consumption_monotonic is False
        or cost_monotonic is False
    ):
        status = "incomplete"
    else:
        status = "ok"

    return EnergyStatisticsHealth(
        status=status,
        checked_at=checked_at,
        portal_latest_timestamp=portal_latest_timestamp,
        consumption_earliest_timestamp=consumption_starts[0]
        if consumption_starts
        else None,
        consumption_latest_timestamp=consumption_starts[-1]
        if consumption_starts
        else None,
        cost_earliest_timestamp=cost_starts[0] if cost_starts else None,
        cost_latest_timestamp=cost_starts[-1] if cost_starts else None,
        expected_consumption_points=len(expected_consumption),
        expected_cost_points=len(expected_cost),
        consumption_point_count=len(consumption_starts_raw),
        cost_point_count=len(cost_starts_raw),
        missing_consumption_points=missing_consumption,
        missing_cost_points=missing_cost,
        consumption_internal_gaps=_internal_gaps(consumption_starts),
        cost_internal_gaps=_internal_gaps(cost_starts),
        consumption_duplicate_timestamps=consumption_duplicates,
        cost_duplicate_timestamps=cost_duplicates,
        consumption_sum_monotonic=consumption_monotonic,
        cost_sum_monotonic=cost_monotonic,
        write_pending=write_pending,
    )


def energy_statistics_error(
    error: Exception, *, checked_at: datetime, write_pending: bool
) -> EnergyStatisticsHealth:
    """Build a safe error snapshot without retaining backend payloads."""
    return EnergyStatisticsHealth(
        status="error",
        checked_at=checked_at,
        write_pending=write_pending,
        error_type=type(error).__name__,
        error=sanitize_error_message(error),
    )


def assess_meter_register(
    readings: Iterable[MeterReading], *, checked_at: datetime
) -> MeterRegisterHealth:
    """Detect lower register readings without altering consumption math."""
    decreases: list[datetime] = []
    previous: float | None = None
    for reading in sorted(readings, key=lambda item: item.timestamp):
        if previous is not None and reading.meter_state_m3 < previous:
            decreases.append(reading.timestamp)
        previous = reading.meter_state_m3
    return MeterRegisterHealth(
        status=("possible_reset_or_correction" if decreases else "normal"),
        new_decrease_timestamp=decreases[-1] if decreases else None,
        decrease_count_current_readings=len(decreases),
        last_check_at=checked_at,
    )
