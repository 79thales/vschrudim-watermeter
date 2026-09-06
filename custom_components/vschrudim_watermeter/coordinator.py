"""Coordinator for VSChrudim watermeter."""
from __future__ import annotations
from datetime import timedelta
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .api import VsChrudimAuthError, VsChrudimClient, VsChrudimError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .models import ConsumptionPlace, WaterMeterData

_LOGGER = logging.getLogger(__name__)

class VsChrudimCoordinator(DataUpdateCoordinator[WaterMeterData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: VsChrudimClient, place: ConsumptionPlace) -> None:
        interval = timedelta(minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds() / 60))
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=interval, always_update=False)
        self.client = client
        self.place = place

    async def _async_update_data(self) -> WaterMeterData:
        try:
            return await self.client.async_get_data(self.place)
        except VsChrudimAuthError as err:
            raise ConfigEntryAuthFailed from err
        except VsChrudimError as err:
            raise UpdateFailed(str(err)) from err
