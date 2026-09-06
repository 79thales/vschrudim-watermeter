"""Redacted diagnostic export."""
from __future__ import annotations
from dataclasses import asdict
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data
from .const import CONF_PASSWORD, CONF_USERNAME
from .coordinator import VsChrudimCoordinator

async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry[VsChrudimCoordinator]):
    coordinator = entry.runtime_data
    return async_redact_data({"entry": {"data": dict(entry.data), "options": dict(entry.options)}, "place": asdict(coordinator.place), "reading_count": len(coordinator.data.readings), "latest_timestamp": coordinator.data.readings[-1].timestamp.isoformat() if coordinator.data.readings else None, "missing_hourly_readings": len(coordinator.data.missing_timestamps), "recovery_attempts": coordinator.data.recovery_attempts}, {CONF_PASSWORD, CONF_USERNAME})
