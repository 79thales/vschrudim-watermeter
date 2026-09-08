"""Bounded, privacy-safe download-attempt diagnostics."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
import re
from typing import Final

MAX_DOWNLOAD_ATTEMPTS: Final = 30
_VALID_RESULTS: Final = frozenset({"success", "failed", "authentication_failed"})
_VALID_SOURCES: Final = frozenset(
    {"csv_link", "csv_postback", "csv_submit", "html_table", "unknown"}
)
_VALID_PORTAL_PAGE_FEATURES: Final = frozenset(
    {
        "webforms_form",
        "html_table",
        "csv_export_link",
        "csv_export_postback",
        "csv_export_submit",
    }
)
_VALID_READING_QUALITY_FLAGS: Final = frozenset(
    {
        "negative_meter_state",
        "non_finite_meter_state",
        "meter_state_decreased",
        "timestamps_not_strictly_increasing",
    }
)
_MAX_ERROR_LENGTH: Final = 300


def sanitize_error_message(value: object) -> str | None:
    """Return a short diagnostic message without credentials or URLs."""
    if value is None:
        return None
    message = " ".join(str(value).split())
    if not message:
        return None
    message = re.sub(r"https?://[^\s]+", "[redacted URL]", message, flags=re.I)
    message = re.sub(
        r"(?i)\b(password|username|cookie|session|token|authorization)\b\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        message,
    )
    return message[:_MAX_ERROR_LENGTH]


def _timestamp(value: object) -> str | None:
    """Accept only ISO-8601 timestamps for persisted diagnostic records."""
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def _non_negative_int(value: object, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _safe_values(value: object, allowed: frozenset[str]) -> tuple[str, ...]:
    """Keep only known, non-sensitive diagnostic vocabulary values."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item in allowed)


@dataclass(frozen=True, slots=True)
class DownloadAttempt:
    """One completed top-level coordinator update attempt."""

    started_at: str
    finished_at: str
    duration_ms: int
    result: str
    source: str = "unknown"
    reading_count: int = 0
    latest_timestamp: str | None = None
    missing_hourly_readings: int | None = None
    recovery_attempts: int = 0
    error_type: str | None = None
    error: str | None = None
    export_candidates_found: int = 0
    export_candidates_attempted: int = 0
    html_table_detected: bool = False
    portal_page_features: tuple[str, ...] = ()
    reading_quality_flags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Serialize only the explicitly safe diagnostic fields."""
        value = asdict(self)
        value["error"] = sanitize_error_message(self.error)
        value["portal_page_features"] = list(
            _safe_values(self.portal_page_features, _VALID_PORTAL_PAGE_FEATURES)
        )
        value["reading_quality_flags"] = list(
            _safe_values(
                self.reading_quality_flags, _VALID_READING_QUALITY_FLAGS
            )
        )
        return value

    @classmethod
    def from_dict(cls, value: object) -> DownloadAttempt | None:
        """Read one stored record defensively and discard malformed data."""
        if not isinstance(value, dict):
            return None
        started_at = _timestamp(value.get("started_at"))
        finished_at = _timestamp(value.get("finished_at"))
        result = value.get("result")
        if not started_at or not finished_at or result not in _VALID_RESULTS:
            return None
        source = value.get("source")
        source = source if source in _VALID_SOURCES else "unknown"
        latest_timestamp = _timestamp(value.get("latest_timestamp"))
        missing = value.get("missing_hourly_readings")
        missing_value = (
            _non_negative_int(missing) if missing is not None else None
        )
        error_type = value.get("error_type")
        return cls(
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=_non_negative_int(value.get("duration_ms")),
            result=result,
            source=source,
            reading_count=_non_negative_int(value.get("reading_count")),
            latest_timestamp=latest_timestamp,
            missing_hourly_readings=missing_value,
            recovery_attempts=_non_negative_int(value.get("recovery_attempts")),
            error_type=str(error_type)[:80] if error_type else None,
            error=sanitize_error_message(value.get("error")),
            export_candidates_found=_non_negative_int(
                value.get("export_candidates_found")
            ),
            export_candidates_attempted=_non_negative_int(
                value.get("export_candidates_attempted")
            ),
            html_table_detected=bool(value.get("html_table_detected")),
            portal_page_features=_safe_values(
                value.get("portal_page_features"), _VALID_PORTAL_PAGE_FEATURES
            ),
            reading_quality_flags=_safe_values(
                value.get("reading_quality_flags"),
                _VALID_READING_QUALITY_FLAGS,
            ),
        )


def load_attempt_history(value: object) -> list[DownloadAttempt]:
    """Restore only valid records and retain the newest bounded history."""
    if not isinstance(value, list):
        return []
    records = [record for item in value if (record := DownloadAttempt.from_dict(item))]
    return records[-MAX_DOWNLOAD_ATTEMPTS:]


def append_attempt(
    history: Iterable[DownloadAttempt], attempt: DownloadAttempt
) -> list[DownloadAttempt]:
    """Append a completed attempt and discard the oldest excess records."""
    return [*history, attempt][-MAX_DOWNLOAD_ATTEMPTS:]
