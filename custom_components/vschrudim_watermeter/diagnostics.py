"""Redacted diagnostic export."""
from __future__ import annotations
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data
from .const import CONF_PASSWORD, CONF_USERNAME
from .coordinator import VsChrudimCoordinator

async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry[VsChrudimCoordinator]):
    coordinator = entry.runtime_data
    return async_redact_data(
        {
            "entry": {
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "place_configured": True,
            "reading_count": len(coordinator.data.readings),
            "latest_timestamp": coordinator.data.readings[-1].timestamp.isoformat()
            if coordinator.data.readings
            else None,
            "missing_hourly_readings": len(coordinator.data.missing_timestamps),
            "recovery_attempts": coordinator.data.recovery_attempts,
            "last_attempt_at": coordinator.last_attempt_at.isoformat()
            if coordinator.last_attempt_at
            else None,
            "last_success_at": coordinator.last_success_at.isoformat()
            if coordinator.last_success_at
            else None,
            "last_attempt_result": coordinator.last_attempt_result,
            "last_attempt_error": coordinator.last_attempt_error,
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
