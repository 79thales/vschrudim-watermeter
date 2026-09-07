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

    def as_dict(self) -> dict[str, object]:
        """Serialize only the explicitly safe diagnostic fields."""
        value = asdict(self)
        value["error"] = sanitize_error_message(self.error)
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
