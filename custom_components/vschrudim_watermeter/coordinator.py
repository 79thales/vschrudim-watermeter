"""Coordinator for VSChrudim watermeter."""
from __future__ import annotations
import asyncio
from datetime import timedelta
import logging
from homeassistant.components.persistent_notification import async_create, async_dismiss
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .api import VsChrudimAuthError, VsChrudimClient, VsChrudimError
from .calculation import latest_consumption
from .const import (
    CONF_FAILURE_THRESHOLD,
    CONF_MISSING_RETRY_ATTEMPTS,
    CONF_NOTIFY_MISSING,
    CONF_NOTIFY_UNAVAILABLE,
    CONF_RETRY_DELAY,
    CONF_SCAN_INTERVAL,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_MISSING_RETRY_ATTEMPTS,
    DEFAULT_NOTIFY_MISSING,
    DEFAULT_NOTIFY_UNAVAILABLE,
    DEFAULT_RETRY_DELAY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .models import ConsumptionPlace, WaterMeterData
from .recovery import find_missing_hours, merge_readings

_LOGGER = logging.getLogger(__name__)

class VsChrudimCoordinator(DataUpdateCoordinator[WaterMeterData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: VsChrudimClient, place: ConsumptionPlace) -> None:
        interval = timedelta(minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds() / 60))
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=interval, always_update=False)
        self.client = client
        self.place = place
        self.entry = entry
        self._consecutive_failures = 0
        self._known_readings = ()

    @property
    def _unavailable_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_unavailable"

    @property
    def _missing_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_missing_data"

    async def _async_update_data(self) -> WaterMeterData:
        try:
            data = await self.client.async_get_data(self.place)
            merged = merge_readings(self._known_readings, data.readings)
            missing = find_missing_hours(merged)
            attempts = 0
            maximum_attempts = int(self.entry.options.get(CONF_MISSING_RETRY_ATTEMPTS, DEFAULT_MISSING_RETRY_ATTEMPTS))
            retry_delay = int(self.entry.options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY))
            while missing and attempts < maximum_attempts:
                attempts += 1
                await asyncio.sleep(retry_delay)
                retry_data = await self.client.async_get_data(self.place)
                merged = merge_readings(merged, retry_data.readings)
                missing = find_missing_hours(merged)
            self._known_readings = merged
            self._consecutive_failures = 0
            async_dismiss(self.hass, self._unavailable_notification_id)
            if missing and self.entry.options.get(CONF_NOTIFY_MISSING, DEFAULT_NOTIFY_MISSING):
                preview = ", ".join(item.isoformat(timespec="minutes") for item in missing[:10])
                suffix = " …" if len(missing) > 10 else ""
                async_create(
                    self.hass,
                    f"The portal still has {len(missing)} missing hourly reading(s) after {attempts} recovery attempt(s): {preview}{suffix}",
                    title="VSChrudim watermeter – missing data",
                    notification_id=self._missing_notification_id,
                )
            else:
                async_dismiss(self.hass, self._missing_notification_id)
            return WaterMeterData(self.place, merged, latest_consumption(merged), missing, attempts)
        except VsChrudimAuthError as err:
            raise ConfigEntryAuthFailed from err
        except VsChrudimError as err:
            self._consecutive_failures += 1
            threshold = int(self.entry.options.get(CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD))
            if self._consecutive_failures >= threshold and self.entry.options.get(CONF_NOTIFY_UNAVAILABLE, DEFAULT_NOTIFY_UNAVAILABLE):
                async_create(
                    self.hass,
                    f"The VS Chrudim portal has failed {self._consecutive_failures} consecutive updates. Home Assistant will keep retrying automatically. Last error: {err}",
                    title="VSChrudim watermeter – source unavailable",
                    notification_id=self._unavailable_notification_id,
                )
            raise UpdateFailed(str(err), retry_after=max(60, int(self.entry.options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY)))) from err
