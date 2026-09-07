"""Explicitly confirmed maintenance services for Energy statistics."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

SERVICE_CLEAR_ENERGY_STATISTICS = "clear_energy_statistics"
SERVICE_REBUILD_ENERGY_STATISTICS = "rebuild_energy_statistics"

_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("confirm"): cv.boolean,
    }
)


async def _coordinator_for_call(hass: HomeAssistant, call: ServiceCall):
    """Resolve only a loaded VSChrudim config entry."""
    if call.data["confirm"] is not True:
        raise HomeAssistantError("This destructive service requires confirm=true")
    entry = hass.config_entries.async_get_entry(call.data["entry_id"])
    if entry is None or entry.domain != DOMAIN or entry.runtime_data is None:
        raise HomeAssistantError("The requested VSChrudim config entry is not loaded")
    return entry.runtime_data


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register global handlers once; they resolve the requested entry at call time."""
    if hass.services.has_service(DOMAIN, SERVICE_CLEAR_ENERGY_STATISTICS):
        return

    async def clear_energy_statistics(call: ServiceCall) -> None:
        coordinator = await _coordinator_for_call(hass, call)
        await coordinator.async_clear_energy_statistics(confirm=True)

    async def rebuild_energy_statistics(call: ServiceCall) -> None:
        coordinator = await _coordinator_for_call(hass, call)
        await coordinator.async_rebuild_energy_statistics(confirm=True)

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_ENERGY_STATISTICS,
        clear_energy_statistics,
        schema=_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REBUILD_ENERGY_STATISTICS,
        rebuild_energy_statistics,
        schema=_SERVICE_SCHEMA,
    )
