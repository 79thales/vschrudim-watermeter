"""VSChrudim watermeter integration."""
from __future__ import annotations
import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from .api import VsChrudimClient
from .const import CONF_PLACE, DOMAIN, PLATFORMS
from .coordinator import VsChrudimCoordinator
from .models import ConsumptionPlace

type VsChrudimConfigEntry = ConfigEntry[VsChrudimCoordinator]

async def async_setup_entry(hass: HomeAssistant, entry: VsChrudimConfigEntry) -> bool:
    """Set up from a config entry; session credentials stay in ConfigEntry data."""
    place = ConsumptionPlace(**entry.data[CONF_PLACE])
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    client = VsChrudimClient(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = VsChrudimCoordinator(hass, entry, client, place)
    await coordinator.async_initialize()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_history_backfill(
        resume_only=coordinator.history_backfill_status != "not_started"
    )
    return True

async def async_unload_entry(hass: HomeAssistant, entry: VsChrudimConfigEntry) -> bool:
    """Unload platforms; HA detaches the entry-owned HTTP session."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
