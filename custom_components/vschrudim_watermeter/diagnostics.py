"""Redacted diagnostic export."""
from __future__ import annotations
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data
from .const import CONF_PASSWORD, CONF_USERNAME
from .coordinator import VsChrudimCoordinator
from .attempts import sanitize_error_message

async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry[VsChrudimCoordinator]):
    coordinator = entry.runtime_data
    data = coordinator.data
    energy_health = getattr(coordinator, "energy_statistics_health", None)
    meter_health = getattr(coordinator, "meter_register_health", None)
    energy_health_data = (
        energy_health.as_dict()
        if energy_health is not None and hasattr(energy_health, "as_dict")
        else {"status": "unknown"}
    )
    meter_health_data = (
        meter_health.as_dict()
        if meter_health is not None and hasattr(meter_health, "as_dict")
        else {"status": "normal"}
    )
    return async_redact_data(
        {
            "entry": {
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "place_configured": True,
            "reading_count": len(data.readings) if data else 0,
            "latest_timestamp": data.readings[-1].timestamp.isoformat()
            if data and data.readings
            else None,
            "missing_hourly_readings": len(data.missing_timestamps) if data else None,
            "recovery_attempts": data.recovery_attempts if data else 0,
            "last_attempt_at": coordinator.last_attempt_at.isoformat()
            if coordinator.last_attempt_at
            else None,
            "last_success_at": coordinator.last_success_at.isoformat()
            if coordinator.last_success_at
            else None,
            "last_attempt_result": coordinator.last_attempt_result,
            "last_attempt_error": sanitize_error_message(coordinator.last_attempt_error),
            "source_status": coordinator.source_status,
            "last_download_source": coordinator.last_download_source,
            "last_download_latest_timestamp": coordinator.last_download_latest_timestamp.isoformat()
            if coordinator.last_download_latest_timestamp
            else None,
            "last_download_reading_count": coordinator.last_download_reading_count,
            "last_portal_page_features": list(
                coordinator.last_portal_page_features
            ),
            "last_reading_quality_flags": list(
                coordinator.last_reading_quality_flags
            ),
            "duplicate_readings_merged": coordinator.last_duplicate_readings_merged,
            "missing_readings_recovered": coordinator.last_missing_readings_recovered,
            "current_missing_hourly_readings": coordinator.current_missing_hourly_readings,
            "oldest_missing_hour": coordinator.oldest_missing_hour.isoformat()
            if coordinator.oldest_missing_hour
            else None,
            "last_test_download": {
                "at": coordinator.last_test_download_at.isoformat()
                if coordinator.last_test_download_at
                else None,
                "result": coordinator.last_test_download_result,
                "error": sanitize_error_message(coordinator.last_test_download_error),
            },
            "download_attempt_history": [
                attempt.as_dict()
                for attempt in reversed(coordinator.download_attempt_history)
            ],
            "energy_statistics": {
                "consumption_statistic_id": coordinator.consumption_statistic_id,
                "cost_statistic_id": coordinator.cost_statistic_id,
                "ready": coordinator.statistics_ready,
            },
            "energy_statistics_health": {
                **energy_health_data,
                "write_status": getattr(
                    coordinator, "statistics_write_status", "never"
                ),
                "last_attempt_at": (
                    coordinator.statistics_last_attempt_at.isoformat()
                    if getattr(coordinator, "statistics_last_attempt_at", None)
                    else None
                ),
                "last_success_at": (
                    coordinator.statistics_last_success_at.isoformat()
                    if getattr(coordinator, "statistics_last_success_at", None)
                    else None
                ),
                "last_error_type": getattr(
                    coordinator, "statistics_last_error_type", None
                ),
                "last_error": sanitize_error_message(
                    getattr(coordinator, "statistics_last_error", None)
                ),
                "last_written_points": getattr(
                    coordinator, "statistics_last_written_points", 0
                ),
                "recovery_pending": getattr(
                    coordinator, "statistics_recovery_pending", False
                ),
            },
            "meter_register_health": meter_health_data,
            "history": {
                "status": coordinator.history_backfill_status,
                "scan_start": coordinator.history_backfill_scan_start.isoformat()
                if coordinator.history_backfill_scan_start
                else None,
                "cursor": coordinator.history_backfill_cursor.isoformat()
                if coordinator.history_backfill_cursor
                else None,
                "earliest_date": coordinator.history_earliest_date.isoformat()
                if coordinator.history_earliest_date
                else None,
                "processed_chunks": coordinator.history_backfill_processed_chunks,
                "total_chunks": coordinator.history_backfill_total_chunks,
                "imported_hours": coordinator.history_backfill_imported_hours,
                "error": coordinator.history_backfill_error,
            },
        },
        {CONF_PASSWORD, CONF_USERNAME, "place"},
    )
