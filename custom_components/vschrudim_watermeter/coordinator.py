"""Coordinator for VSChrudim watermeter."""
from __future__ import annotations
import asyncio
from datetime import date, datetime, timedelta
import logging
from homeassistant.components.persistent_notification import async_create, async_dismiss
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from .api import (
    VsChrudimAuthError,
    VsChrudimClient,
    VsChrudimError,
    is_empty_history_boundary_error,
)
from .calculation import latest_consumption
from .const import (
    CONF_FAILURE_THRESHOLD,
    CONF_MISSING_RETRY_ATTEMPTS,
    CONF_NOTIFY_MISSING,
    CONF_NOTIFY_UNAVAILABLE,
    CONF_PRICE_PER_M3,
    CONF_RETRY_DELAY,
    CONF_SCAN_INTERVAL,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_MISSING_RETRY_ATTEMPTS,
    DEFAULT_NOTIFY_MISSING,
    DEFAULT_NOTIFY_UNAVAILABLE,
    DEFAULT_PRICE_PER_M3,
    DEFAULT_RETRY_DELAY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .models import ConsumptionPlace, WaterMeterData
from .history import (
    async_import_cost_history,
    async_import_meter_history,
    history_ranges_backwards,
)
from .recovery import find_missing_hours, merge_readings

_LOGGER = logging.getLogger(__name__)
_HISTORY_STORE_VERSION = 1
_BACKFILL_REQUEST_DELAY = 2


def _stored_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _stored_datetime(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _three_years_ago(today: date) -> date:
    """Return a calendar-safe three-year history boundary."""
    try:
        return today.replace(year=today.year - 3)
    except ValueError:
        return today.replace(year=today.year - 3, day=28)

class VsChrudimCoordinator(DataUpdateCoordinator[WaterMeterData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: VsChrudimClient, place: ConsumptionPlace) -> None:
        interval = timedelta(minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds() / 60))
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=interval, always_update=False)
        self.client = client
        self.place = place
        self.entry = entry
        self._consecutive_failures = 0
        self._known_readings = ()
        self._api_lock = asyncio.Lock()
        self._meter_entity_id: str | None = None
        self._cost_entity_id: str | None = None
        self.last_attempt_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_attempt_result = "never"
        self.last_attempt_error: str | None = None
        self.history_backfill_status = "not_started"
        self.history_backfill_started_at: datetime | None = None
        self.history_backfill_completed_at: datetime | None = None
        self.history_backfill_scan_start: date | None = None
        self.history_backfill_cursor: date | None = None
        self.history_earliest_date: date | None = None
        self.history_backfill_processed_chunks = 0
        self.history_backfill_total_chunks = 0
        self.history_backfill_imported_hours = 0
        self.history_backfill_error: str | None = None
        self._history_backfill_task: asyncio.Task[None] | None = None
        self._history_store: Store[dict] = Store(
            hass,
            _HISTORY_STORE_VERSION,
            f"{DOMAIN}.history_backfill.{entry.entry_id}",
        )

    async def async_initialize(self) -> None:
        """Restore non-sensitive history progress after restart."""
        stored = await self._history_store.async_load()
        if not isinstance(stored, dict):
            return
        status = str(stored.get("status") or "not_started")
        configured_price = float(
            self.entry.options.get(CONF_PRICE_PER_M3, DEFAULT_PRICE_PER_M3)
        )
        stored_price = stored.get("price_per_m3")
        try:
            price_changed = (
                stored_price is None or float(stored_price) != configured_price
            )
        except (TypeError, ValueError):
            price_changed = True
        if price_changed:
            # A new/changed tariff needs a complete idempotent pass so every
            # imported meter hour receives its matching cost statistic.
            status = "not_started"
            stored = {}
        if status in {"not_started", "running", "paused", "failed", "completed"}:
            self.history_backfill_status = status
        self.history_backfill_started_at = _stored_datetime(stored.get("started_at"))
        self.history_backfill_completed_at = _stored_datetime(stored.get("completed_at"))
        self.history_backfill_scan_start = _stored_date(stored.get("scan_start"))
        self.history_backfill_cursor = _stored_date(stored.get("cursor"))
        self.history_earliest_date = _stored_date(stored.get("earliest_date"))
        self.history_backfill_processed_chunks = max(
            0, int(stored.get("processed_chunks") or 0)
        )
        self.history_backfill_total_chunks = max(
            0, int(stored.get("total_chunks") or 0)
        )
        self.history_backfill_imported_hours = max(
            0, int(stored.get("imported_hours") or 0)
        )
        error = stored.get("error")
        self.history_backfill_error = str(error) if error else None

    @property
    def _unavailable_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_unavailable"

    @property
    def _missing_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_missing_data"

    @property
    def _history_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_history_backfill"

    async def _async_update_data(self) -> WaterMeterData:
        self.last_attempt_at = dt_util.now()
        self.last_attempt_result = "running"
        self.last_attempt_error = None
        try:
            async with self._api_lock:
                data = await self.client.async_get_data(self.place)
            merged = merge_readings(self._known_readings, data.readings)
            missing = find_missing_hours(merged)
            attempts = 0
            maximum_attempts = int(self.entry.options.get(CONF_MISSING_RETRY_ATTEMPTS, DEFAULT_MISSING_RETRY_ATTEMPTS))
            retry_delay = int(self.entry.options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY))
            while missing and attempts < maximum_attempts:
                attempts += 1
                await asyncio.sleep(retry_delay)
                async with self._api_lock:
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
            result = WaterMeterData(self.place, merged, latest_consumption(merged), missing, attempts)
            self.last_success_at = dt_util.now()
            self.last_attempt_result = "success"
            self.last_attempt_error = None
            self._async_import_readings(merged)
            if self.history_backfill_status in {"paused", "failed"}:
                self.hass.loop.call_soon(
                    self.async_start_history_backfill,
                    True,
                )
            return result
        except VsChrudimAuthError as err:
            self.last_attempt_result = "authentication_failed"
            self.last_attempt_error = "Authentication is no longer valid"
            raise ConfigEntryAuthFailed from err
        except VsChrudimError as err:
            self.last_attempt_result = "failed"
            self.last_attempt_error = str(err)
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

    def async_register_meter_entity(self, entity_id: str) -> None:
        """Register the real entity statistic ID after entity setup."""
        self._meter_entity_id = entity_id
        self._async_import_readings(self._known_readings)

    def async_register_cost_entity(self, entity_id: str) -> None:
        """Register the cumulative cost statistic used by the Energy dashboard."""
        self._cost_entity_id = entity_id
        self._async_import_readings(self._known_readings)

    def _async_import_readings(self, readings: tuple) -> int:
        """Queue completed hours under the Energy-selectable sensor ID."""
        if not self._meter_entity_id or not readings:
            return 0
        try:
            local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
            now = dt_util.now()
            imported = async_import_meter_history(
                self.hass,
                entity_id=self._meter_entity_id,
                readings=readings,
                local_tz=local_tz,
                now=now,
            )
            if self._cost_entity_id:
                async_import_cost_history(
                    self.hass,
                    entity_id=self._cost_entity_id,
                    readings=readings,
                    price_per_m3=float(
                        self.entry.options.get(
                            CONF_PRICE_PER_M3, DEFAULT_PRICE_PER_M3
                        )
                    ),
                    currency="CZK",
                    local_tz=local_tz,
                    now=now,
                )
            return imported
        except HomeAssistantError as err:
            _LOGGER.warning("Could not import water-meter history: %s", err)
            return 0

    def async_start_history_backfill(self, resume_only: bool = False) -> bool:
        """Start or resume the bounded three-year history scan."""
        if self._history_backfill_task and not self._history_backfill_task.done():
            return False
        resumable = (
            self.history_backfill_status in {"running", "paused", "failed"}
            and self.history_backfill_cursor is not None
        )
        if resume_only and not resumable:
            return False
        today = dt_util.now().date()
        if not resumable:
            self.history_backfill_scan_start = _three_years_ago(today)
            self.history_backfill_cursor = today
            self.history_backfill_started_at = dt_util.now()
            self.history_backfill_completed_at = None
            self.history_backfill_processed_chunks = 0
            self.history_backfill_total_chunks = len(
                history_ranges_backwards(self.history_backfill_scan_start, today)
            )
            self.history_backfill_imported_hours = 0
        self.history_backfill_status = "running"
        self.history_backfill_error = None
        async_dismiss(self.hass, self._history_notification_id)
        self.async_update_listeners()
        task = self.entry.async_create_background_task(
            self.hass,
            self._async_backfill_history(),
            f"{DOMAIN} history backfill {self.entry.entry_id}",
        )
        self._history_backfill_task = task
        task.add_done_callback(self._history_backfill_done)
        return True

    def _history_backfill_done(self, task: asyncio.Task[None]) -> None:
        if self._history_backfill_task is task:
            self._history_backfill_task = None

    async def _async_backfill_history(self) -> None:
        """Fetch, import and checkpoint monthly ranges from newest to oldest."""
        try:
            await self._async_save_history_state()
            scan_start = self.history_backfill_scan_start
            cursor = self.history_backfill_cursor
            if scan_start is None or cursor is None or cursor < scan_start:
                await self._async_finish_history_backfill()
                return
            for chunk_from, chunk_to in history_ranges_backwards(scan_start, cursor):
                readings = None
                maximum_attempts = max(
                    1,
                    int(
                        self.entry.options.get(
                            CONF_MISSING_RETRY_ATTEMPTS,
                            DEFAULT_MISSING_RETRY_ATTEMPTS,
                        )
                    )
                    + 1,
                )
                for attempt in range(maximum_attempts):
                    try:
                        async with self._api_lock:
                            readings = await self.client.async_get_history(
                                self.place, chunk_from, chunk_to
                            )
                        break
                    except VsChrudimError as err:
                        if is_empty_history_boundary_error(
                            err,
                            requested_to=chunk_to,
                            earliest_reading=self.history_earliest_date,
                        ):
                            _LOGGER.info(
                                "Water-meter history begins on %s; older empty "
                                "portal ranges will not be requested",
                                self.history_earliest_date,
                            )
                            await self._async_finish_history_backfill()
                            return
                        if attempt + 1 >= maximum_attempts:
                            raise
                        await asyncio.sleep(
                            int(
                                self.entry.options.get(
                                    CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY
                                )
                            )
                        )
                assert readings is not None
                self._known_readings = merge_readings(self._known_readings, readings)
                self.history_backfill_imported_hours += self._async_import_readings(
                    readings
                )
                if readings:
                    earliest = min(item.timestamp.date() for item in readings)
                    if (
                        self.history_earliest_date is None
                        or earliest < self.history_earliest_date
                    ):
                        self.history_earliest_date = earliest
                self.history_backfill_cursor = chunk_from - timedelta(days=1)
                self.history_backfill_processed_chunks += 1
                await self._async_save_history_state()
                self.async_update_listeners()
                await asyncio.sleep(_BACKFILL_REQUEST_DELAY)
            await self._async_finish_history_backfill()
        except asyncio.CancelledError:
            self.history_backfill_status = "paused"
            await self._async_save_history_state()
            self.async_update_listeners()
            raise
        except VsChrudimAuthError:
            await self._async_fail_history_backfill(
                "Authentication is no longer valid"
            )
        except (VsChrudimError, HomeAssistantError, ValueError) as err:
            await self._async_fail_history_backfill(str(err))

    async def _async_finish_history_backfill(self) -> None:
        self.history_backfill_status = "completed"
        self.history_backfill_cursor = self.history_backfill_scan_start
        self.history_backfill_processed_chunks = self.history_backfill_total_chunks
        self.history_backfill_completed_at = dt_util.now()
        self.history_backfill_error = None
        async_dismiss(self.hass, self._history_notification_id)
        await self._async_save_history_state()
        self.async_update_listeners()

    async def _async_fail_history_backfill(self, error: str) -> None:
        self.history_backfill_status = "failed"
        self.history_backfill_error = error
        await self._async_save_history_state()
        self.async_update_listeners()
        if self.entry.options.get(CONF_NOTIFY_MISSING, DEFAULT_NOTIFY_MISSING):
            async_create(
                self.hass,
                "The hourly history scan could not finish. Its checkpoint was "
                "saved and it will be retried after a later successful update. "
                f"Last error: {error}",
                title="VSChrudim watermeter – history incomplete",
                notification_id=self._history_notification_id,
            )
        _LOGGER.warning("Could not backfill all water-meter history: %s", error)

    async def _async_save_history_state(self) -> None:
        await self._history_store.async_save(
            {
                "status": self.history_backfill_status,
                "started_at": self.history_backfill_started_at.isoformat()
                if self.history_backfill_started_at
                else None,
                "completed_at": self.history_backfill_completed_at.isoformat()
                if self.history_backfill_completed_at
                else None,
                "scan_start": self.history_backfill_scan_start.isoformat()
                if self.history_backfill_scan_start
                else None,
                "cursor": self.history_backfill_cursor.isoformat()
                if self.history_backfill_cursor
                else None,
                "earliest_date": self.history_earliest_date.isoformat()
                if self.history_earliest_date
                else None,
                "processed_chunks": self.history_backfill_processed_chunks,
                "total_chunks": self.history_backfill_total_chunks,
                "imported_hours": self.history_backfill_imported_hours,
                "error": self.history_backfill_error,
                "price_per_m3": float(
                    self.entry.options.get(CONF_PRICE_PER_M3, DEFAULT_PRICE_PER_M3)
                ),
            }
        )
