"""VSChrudim watermeter integration."""
from __future__ import annotations

import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import VsChrudimClient
from .const import CONF_PLACE, PLATFORMS
from .const import DOMAIN as DOMAIN
from .coordinator import VsChrudimCoordinator
from .models import ConsumptionPlace
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

type VsChrudimConfigEntry = ConfigEntry[VsChrudimCoordinator]

async def async_setup_entry(hass: HomeAssistant, entry: VsChrudimConfigEntry) -> bool:
    """Set up from a config entry; session credentials stay in ConfigEntry data."""
    await async_setup_services(hass)
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
    # Panel je pouze doplněk: jeho selhání nikdy nezablokuje načítání odečtů.
    try:
        from .dashboard_api import async_setup_dashboard_generator
        await async_setup_dashboard_generator(hass)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Generátor dashboardu není dostupný (%s)", type(err).__name__)
    coordinator.async_start_history_backfill(
        resume_only=coordinator.history_backfill_status != "not_started"
    )
    return True

async def async_unload_entry(hass: HomeAssistant, entry: VsChrudimConfigEntry) -> bool:
    """Unload platforms; HA detaches the entry-owned HTTP session."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
