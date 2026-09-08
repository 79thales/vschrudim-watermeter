"""Coordinator for VSChrudim watermeter."""
from __future__ import annotations
import asyncio
from datetime import date, datetime, timedelta
import logging
import math
from time import monotonic
from homeassistant.components.persistent_notification import async_create, async_dismiss
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    get_last_statistics,
    statistics_during_period,
)
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
    CONF_SOURCE_DELAY_WARNING_HOURS,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_MISSING_RETRY_ATTEMPTS,
    DEFAULT_NOTIFY_MISSING,
    DEFAULT_NOTIFY_UNAVAILABLE,
    DEFAULT_PRICE_PER_M3,
    DEFAULT_RETRY_DELAY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOURCE_DELAY_WARNING_HOURS,
    DOMAIN,
)
from .models import ConsumptionPlace, DownloadMetadata, MeterReading, WaterMeterData
from .history import (
    async_add_external_cost_statistics,
    async_add_external_meter_statistics,
    cost_statistics,
    history_ranges_backwards,
    meter_statistics,
)
from .attempts import DownloadAttempt, append_attempt, load_attempt_history, sanitize_error_message
from .recovery import count_duplicate_readings, find_missing_hours, merge_readings

_LOGGER = logging.getLogger(__name__)
_HISTORY_STORE_VERSION = 1
_ATTEMPT_STORE_VERSION = 1
_BACKFILL_REQUEST_DELAY = 2
_STATISTICS_OPERATION_TIMEOUT = 30


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
        self._statistics_operation_lock = asyncio.Lock()
        self._statistics_writes_paused = False
        self.statistics_ready = True
        self.last_attempt_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_attempt_result = "never"
        self.last_attempt_error: str | None = None
        self.source_status = "unknown"
        self.last_download_source = "unknown"
        self.last_download_latest_timestamp: datetime | None = None
        self.last_download_reading_count = 0
        self.last_duplicate_readings_merged = 0
        self.last_missing_readings_recovered = 0
        self.current_missing_hourly_readings = 0
        self.oldest_missing_hour: datetime | None = None
        self.last_test_download_at: datetime | None = None
        self.last_test_download_result = "never"
        self.last_test_download_error: str | None = None
        self._source_problem_notification_active = False
        self._missing_problem_notification_active = False
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
        self._attempt_store: Store[list[dict[str, object]]] = Store(
            hass,
            _ATTEMPT_STORE_VERSION,
            f"{DOMAIN}.download_attempts.{entry.entry_id}",
        )
        self.download_attempt_history: list[DownloadAttempt] = []

    async def async_initialize(self) -> None:
        """Restore non-sensitive history progress after restart."""
        attempts = await self._attempt_store.async_load()
        self.download_attempt_history = load_attempt_history(attempts)
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

    @property
    def _source_recovered_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_source_recovered"

    @property
    def _missing_recovered_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_missing_data_recovered"

    def _source_status_for_readings(
        self, readings: tuple[MeterReading, ...]
    ) -> str:
        """Classify source freshness only when the user configured a limit."""
        warning_hours = int(
            self.entry.options.get(
                CONF_SOURCE_DELAY_WARNING_HOURS,
                DEFAULT_SOURCE_DELAY_WARNING_HOURS,
            )
        )
        if warning_hours <= 0 or not readings:
            return "ok"
        local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
        now_local = dt_util.now().astimezone(local_tz).replace(tzinfo=None)
        latest = readings[-1].timestamp
        if now_local - latest > timedelta(hours=warning_hours):
            return "delayed_data"
        return "ok"

    def _set_source_status(
        self,
        status: str,
        *,
        error: Exception | None = None,
        notify_problem: bool = False,
    ) -> None:
        """Update source state and notify once per problem-state transition."""
        previous = self.source_status
        self.source_status = status

        if status in {"authentication_required", "error"}:
            if (
                notify_problem
                and self.entry.options.get(
                    CONF_NOTIFY_UNAVAILABLE,
                    DEFAULT_NOTIFY_UNAVAILABLE,
                )
                and (previous != status or not self._source_problem_notification_active)
            ):
                async_dismiss(self.hass, self._source_recovered_notification_id)
                message = (
                    "Home Assistant needs you to reauthenticate the VS Chrudim "
                    "watermeter integration."
                    if status == "authentication_required"
                    else "The VS Chrudim portal is unavailable. Home Assistant "
                    "will keep retrying automatically. Last error: "
                    f"{sanitize_error_message(error) or 'Unknown error'}"
                )
                async_create(
                    self.hass,
                    message,
                    title="VSChrudim watermeter – source needs attention",
                    notification_id=self._unavailable_notification_id,
                )
                self._source_problem_notification_active = True
            return

        async_dismiss(self.hass, self._unavailable_notification_id)
        notifications_enabled = self.entry.options.get(
            CONF_NOTIFY_UNAVAILABLE, DEFAULT_NOTIFY_UNAVAILABLE
        )
        if (
            status == "ok"
            and notifications_enabled
            and self._source_problem_notification_active
        ):
            async_create(
                self.hass,
                "A VS Chrudim portal download succeeded again.",
                title="VSChrudim watermeter – source restored",
                notification_id=self._source_recovered_notification_id,
            )
        if status == "ok":
            self._source_problem_notification_active = False

    def _handle_missing_hours_notification(
        self, missing: tuple[datetime, ...], recovery_attempts: int
    ) -> None:
        """Notify only when missing readings appear or are fully recovered."""
        notify_missing = self.entry.options.get(
            CONF_NOTIFY_MISSING, DEFAULT_NOTIFY_MISSING
        )
        if missing and notify_missing:
            if not self._missing_problem_notification_active:
                preview = ", ".join(
                    item.isoformat(timespec="minutes") for item in missing[:10]
                )
                suffix = " …" if len(missing) > 10 else ""
                async_dismiss(self.hass, self._missing_recovered_notification_id)
                async_create(
                    self.hass,
                    "The portal still has "
                    f"{len(missing)} missing hourly reading(s) after "
                    f"{recovery_attempts} recovery attempt(s): {preview}{suffix}",
                    title="VSChrudim watermeter – missing data",
                    notification_id=self._missing_notification_id,
                )
                self._missing_problem_notification_active = True
            return

        async_dismiss(self.hass, self._missing_notification_id)
        if self._missing_problem_notification_active and notify_missing:
            async_create(
                self.hass,
                "All previously reported missing hourly readings are available again.",
                title="VSChrudim watermeter – data recovered",
                notification_id=self._missing_recovered_notification_id,
            )
        self._missing_problem_notification_active = False

    async def _async_update_data(self) -> WaterMeterData:
        started_at = dt_util.now()
        started_monotonic = monotonic()
        self.last_attempt_at = started_at
        self.last_attempt_result = "running"
        self.last_attempt_error = None
        try:
            async with self._api_lock:
                data = await self.client.async_get_data(self.place)
            duplicate_readings = count_duplicate_readings(
                self._known_readings, data.readings
            )
            merged = merge_readings(self._known_readings, data.readings)
            initially_missing = find_missing_hours(merged)
            missing = initially_missing
            attempts = 0
            maximum_attempts = int(self.entry.options.get(CONF_MISSING_RETRY_ATTEMPTS, DEFAULT_MISSING_RETRY_ATTEMPTS))
            retry_delay = int(self.entry.options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY))
            while missing and attempts < maximum_attempts:
                attempts += 1
                await asyncio.sleep(retry_delay)
                async with self._api_lock:
                    retry_data = await self.client.async_get_data(self.place)
                duplicate_readings += count_duplicate_readings(
                    merged, retry_data.readings
                )
                merged = merge_readings(merged, retry_data.readings)
                missing = find_missing_hours(merged)
            self._known_readings = merged
            self._consecutive_failures = 0
            self.last_download_source = data.download_metadata.source
            self.last_download_latest_timestamp = (
                data.readings[-1].timestamp if data.readings else None
            )
            self.last_download_reading_count = len(data.readings)
            self.last_duplicate_readings_merged = duplicate_readings
            self.last_missing_readings_recovered = len(
                set(initially_missing) - set(missing)
            )
            self.current_missing_hourly_readings = len(missing)
            self.oldest_missing_hour = missing[0] if missing else None
            result = WaterMeterData(
                self.place,
                merged,
                latest_consumption(merged),
                missing,
                attempts,
                data.download_metadata,
            )
            self.last_success_at = dt_util.now()
            self.last_attempt_result = "success"
            self.last_attempt_error = None
            self._set_source_status(self._source_status_for_readings(data.readings))
            self._handle_missing_hours_notification(missing, attempts)
            await self._async_import_readings(merged)
            await self._async_record_download_attempt(
                started_at,
                started_monotonic,
                result="success",
                metadata=data.download_metadata,
                readings=merged,
                missing_hourly_readings=len(missing),
                recovery_attempts=attempts,
            )
            if self.history_backfill_status in {"paused", "failed"}:
                self.hass.loop.call_soon(
                    self.async_start_history_backfill,
                    True,
                )
            return result
        except VsChrudimAuthError as err:
            self.last_attempt_result = "authentication_failed"
            self.last_attempt_error = "Authentication is no longer valid"
            self._set_source_status(
                "authentication_required", error=err, notify_problem=True
            )
            await self._async_record_download_attempt(
                started_at,
                started_monotonic,
                result="authentication_failed",
                error=err,
            )
            raise ConfigEntryAuthFailed from err
        except VsChrudimError as err:
            self.last_attempt_result = "failed"
            self.last_attempt_error = sanitize_error_message(err)
            await self._async_record_download_attempt(
                started_at,
                started_monotonic,
                result="failed",
                metadata=getattr(err, "download_metadata", None),
                error=err,
            )
            self._consecutive_failures += 1
            threshold = int(self.entry.options.get(CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD))
            self._set_source_status(
                "error",
                error=err,
                notify_problem=self._consecutive_failures >= threshold,
            )
            raise UpdateFailed(str(err), retry_after=max(60, int(self.entry.options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY)))) from err

    def async_register_meter_entity(self, entity_id: str) -> None:
        """Remember the legacy live sensor statistic ID for explicit cleanup."""
        self._meter_entity_id = entity_id

    def async_register_cost_entity(self, entity_id: str) -> None:
        """Remember the legacy live-cost statistic ID for explicit cleanup."""
        self._cost_entity_id = entity_id

    @property
    def consumption_statistic_id(self) -> str:
        """Integration-owned statistic ID for Energy water consumption."""
        # Recorder external statistic IDs are slug-only, while Home Assistant
        # config-entry ULIDs are uppercase. Preserve the required ID shape
        # while normalizing the dynamic segment to Recorder's valid form.
        return f"{DOMAIN}:{self.entry.entry_id.casefold()}_water_consumption"

    @property
    def cost_statistic_id(self) -> str:
        """Integration-owned statistic ID for Energy water cost."""
        return f"{DOMAIN}:{self.entry.entry_id.casefold()}_water_cost"

    async def _async_import_readings(
        self, readings: tuple[MeterReading, ...]
    ) -> int:
        """Queue completed hours under the integration-owned statistic IDs."""
        if self._statistics_writes_paused or not readings:
            return 0
        try:
            local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
            now = dt_util.now()
            initial_sum, initial_meter_state = await self._async_statistics_seed(
                readings, local_tz, now
            )
            imported = async_add_external_meter_statistics(
                self.hass,
                statistic_id=self.consumption_statistic_id,
                readings=readings,
                local_tz=local_tz,
                now=now,
                initial_sum=initial_sum,
                initial_meter_state=initial_meter_state,
            )
            async_add_external_cost_statistics(
                self.hass,
                statistic_id=self.cost_statistic_id,
                readings=readings,
                price_per_m3=float(
                    self.entry.options.get(
                        CONF_PRICE_PER_M3, DEFAULT_PRICE_PER_M3
                    )
                ),
                currency="CZK",
                local_tz=local_tz,
                now=now,
                initial_sum=initial_sum,
                initial_meter_state=initial_meter_state,
            )
            return imported
        except HomeAssistantError as err:
            _LOGGER.warning("Could not import water-meter history: %s", err)
            return 0

    async def _async_statistics_seed(
        self,
        readings: tuple[MeterReading, ...],
        local_tz,
        now: datetime,
    ) -> tuple[float, float | None]:
        """Continue a recent external series without resetting its sum on restart."""
        starts = meter_statistics(readings, local_tz=local_tz, now=now)
        if not starts:
            return 0.0, None
        first_start = starts[0]["start"]
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            # Select the last existing point at or before the new batch. A
            # short look-back would reset the cumulative sum after a portal
            # outage longer than one hour. This query is scoped to one
            # integration-owned statistic ID (at most the supported history).
            dt_util.utc_from_timestamp(0),
            first_start + timedelta(seconds=1),
            {self.consumption_statistic_id},
            "hour",
            None,
            {"state", "sum"},
        )
        candidates = rows.get(self.consumption_statistic_id, [])
        if not candidates:
            return 0.0, None
        previous = max(
            candidates,
            key=lambda row: float(row.get("start", 0)),
        )
        try:
            return float(previous.get("sum", 0.0)), float(previous["state"])
        except (KeyError, TypeError, ValueError):
            return 0.0, None

    async def _async_record_download_attempt(
        self,
        started_at: datetime,
        started_monotonic: float,
        *,
        result: str,
        metadata: DownloadMetadata | None = None,
        readings: tuple[MeterReading, ...] = (),
        missing_hourly_readings: int | None = None,
        recovery_attempts: int = 0,
        error: Exception | None = None,
    ) -> None:
        """Persist one privacy-safe record for a complete coordinator update."""
        metadata = metadata or DownloadMetadata()
        finished_at = dt_util.now()
        attempt = DownloadAttempt(
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            duration_ms=max(0, round((monotonic() - started_monotonic) * 1000)),
            result=result,
            source=metadata.source,
            reading_count=len(readings),
            latest_timestamp=readings[-1].timestamp.isoformat() if readings else None,
            missing_hourly_readings=missing_hourly_readings,
            recovery_attempts=recovery_attempts,
            error_type=type(error).__name__ if error else None,
            error=sanitize_error_message(error),
            export_candidates_found=metadata.export_candidates_found,
            export_candidates_attempted=metadata.export_candidates_attempted,
            html_table_detected=metadata.html_table_detected,
        )
        self.download_attempt_history = append_attempt(
            self.download_attempt_history, attempt
        )
        try:
            await self._attempt_store.async_save(
                [item.as_dict() for item in self.download_attempt_history]
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Could not save download-attempt diagnostics: %s", err)

    @property
    def _energy_statistic_ids(self) -> list[str]:
        """Return only integration-owned and known legacy statistic IDs."""
        ids = [self.consumption_statistic_id, self.cost_statistic_id]
        ids.extend(
            entity_id
            for entity_id in (self._meter_entity_id, self._cost_entity_id)
            if entity_id
        )
        return list(dict.fromkeys(ids))

    async def _async_clear_energy_statistics(self) -> None:
        """Wait for Recorder to clear only this integration's statistics."""
        done = asyncio.Event()

        def on_done() -> None:
            self.hass.loop.call_soon_threadsafe(done.set)

        get_instance(self.hass).async_clear_statistics(
            self._energy_statistic_ids, on_done=on_done
        )
        try:
            async with asyncio.timeout(_STATISTICS_OPERATION_TIMEOUT):
                await done.wait()
        except TimeoutError as err:
            raise HomeAssistantError(
                "Timed out while Recorder cleared VSChrudim Energy statistics"
            ) from err

    async def async_clear_energy_statistics(self, *, confirm: bool = False) -> None:
        """Delete Energy statistics only after a caller explicitly confirms."""
        if confirm is not True:
            raise HomeAssistantError("confirm=true is required to clear statistics")
        async with self._statistics_operation_lock:
            self._statistics_writes_paused = True
            self.statistics_ready = False
            await self._async_clear_energy_statistics()

    async def _async_fetch_complete_source_data(self) -> tuple[MeterReading, ...]:
        """Fetch and validate every available portal hour before a rebuild."""
        today = dt_util.now().date()
        scan_start = _three_years_ago(today)
        all_readings: tuple[MeterReading, ...] = ()
        earliest_reading: date | None = None
        for chunk_from, chunk_to in history_ranges_backwards(scan_start, today):
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
            readings: tuple[MeterReading, ...] | None = None
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
                        earliest_reading=earliest_reading,
                    ):
                        readings = ()
                        break
                    if attempt + 1 >= maximum_attempts:
                        raise
                    await asyncio.sleep(
                        int(
                            self.entry.options.get(
                                CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY
                            )
                        )
                    )
            if readings is None:
                raise HomeAssistantError("The complete source validation did not finish")
            if not readings:
                if earliest_reading is not None and chunk_to < earliest_reading:
                    break
                raise HomeAssistantError(
                    "The portal returned an unexpected empty range during source validation"
                )
            if readings:
                earliest_reading = min(
                    earliest_reading or readings[0].timestamp.date(),
                    min(item.timestamp.date() for item in readings),
                )
                all_readings = merge_readings(all_readings, readings)
            await asyncio.sleep(_BACKFILL_REQUEST_DELAY)
        self._validate_complete_source_data(all_readings)
        return all_readings

    @staticmethod
    def _validate_complete_source_data(readings: tuple[MeterReading, ...]) -> None:
        """Reject incomplete or malformed preflight data without deleting stats."""
        if not readings:
            raise HomeAssistantError("The portal returned no readings for statistics rebuild")
        previous: datetime | None = None
        for reading in readings:
            if not math.isfinite(reading.meter_state_m3):
                raise HomeAssistantError("The portal returned a non-finite meter state")
            if previous is not None and reading.timestamp <= previous:
                raise HomeAssistantError("The portal returned non-chronological readings")
            previous = reading.timestamp

    async def _async_verify_energy_statistics(
        self, readings: tuple[MeterReading, ...]
    ) -> None:
        """Confirm Recorder has the newest, monotonic external statistics."""
        local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
        now = dt_util.now()
        expected_by_id = {
            self.consumption_statistic_id: meter_statistics(
                readings, local_tz=local_tz, now=now
            ),
            self.cost_statistic_id: cost_statistics(
                readings,
                price_per_m3=float(
                    self.entry.options.get(CONF_PRICE_PER_M3, DEFAULT_PRICE_PER_M3)
                ),
                local_tz=local_tz,
                now=now,
            ),
        }
        if not all(expected_by_id.values()):
            raise HomeAssistantError("No completed portal hours are available for statistics")
        for _ in range(_STATISTICS_OPERATION_TIMEOUT * 5):
            verified = True
            for statistic_id, expected in expected_by_id.items():
                last = await get_instance(self.hass).async_add_executor_job(
                    get_last_statistics,
                    self.hass,
                    1,
                    statistic_id,
                    True,
                    set(),
                )
                records = last.get(statistic_id, [])
                if not records or records[0].get("start") != expected[-1]["start"].timestamp():
                    verified = False
                    break
                all_stats = await get_instance(self.hass).async_add_executor_job(
                    statistics_during_period,
                    self.hass,
                    expected[0]["start"],
                    None,
                    {statistic_id},
                    "hour",
                    None,
                    {"sum"},
                )
                sums = [
                    float(row["sum"])
                    for row in all_stats.get(statistic_id, [])
                    if row.get("sum") is not None
                ]
                if not sums or not all(
                    current >= prior for prior, current in zip(sums, sums[1:])
                ):
                    verified = False
                    break
            if verified:
                return
            await asyncio.sleep(0.2)
        raise HomeAssistantError("Recorder did not verify rebuilt Energy statistics")

    async def async_rebuild_energy_statistics(self, *, confirm: bool = False) -> None:
        """Safely replace Energy statistics only after complete source validation."""
        if confirm is not True:
            raise HomeAssistantError("confirm=true is required to rebuild statistics")
        async with self._statistics_operation_lock:
            # This deliberate preflight happens before the destructive clear.
            # If the portal is unavailable or its data is malformed, existing
            # Energy statistics remain untouched.
            readings = await self._async_fetch_complete_source_data()
            self._statistics_writes_paused = True
            self.statistics_ready = False
            await self._async_clear_energy_statistics()
            local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
            now = dt_util.now()
            async_add_external_meter_statistics(
                self.hass,
                statistic_id=self.consumption_statistic_id,
                readings=readings,
                local_tz=local_tz,
                now=now,
            )
            async_add_external_cost_statistics(
                self.hass,
                statistic_id=self.cost_statistic_id,
                readings=readings,
                price_per_m3=float(
                    self.entry.options.get(CONF_PRICE_PER_M3, DEFAULT_PRICE_PER_M3)
                ),
                currency="CZK",
                local_tz=local_tz,
                now=now,
            )
            await self._async_verify_energy_statistics(readings)
            self._known_readings = merge_readings(self._known_readings, readings)
            self.statistics_ready = True
            self._statistics_writes_paused = False

    @property
    def statistics_operation_running(self) -> bool:
        """Return whether a clear or rebuild operation is in progress."""
        return self._statistics_operation_lock.locked()

    @property
    def history_backfill_running(self) -> bool:
        """Return whether a historical download is already in progress."""
        return bool(
            self._history_backfill_task
            and not self._history_backfill_task.done()
        )

    async def async_test_download(self) -> None:
        """Validate one current portal download without importing any data.

        This is intentionally separate from the reconciliation button: it
        never changes live sensor data, starts a history scan, or writes
        Energy statistics.
        """
        if self.statistics_operation_running:
            raise HomeAssistantError("Energy statistics maintenance is in progress")

        self.last_test_download_at = dt_util.now()
        self.last_test_download_result = "running"
        self.last_test_download_error = None
        self.async_update_listeners()
        try:
            async with self._api_lock:
                data = await self.client.async_get_data(self.place)
            missing = find_missing_hours(data.readings)
            self.last_test_download_result = "success"
            self.last_download_source = data.download_metadata.source
            self.last_download_latest_timestamp = (
                data.readings[-1].timestamp if data.readings else None
            )
            self.last_download_reading_count = len(data.readings)
            self.last_duplicate_readings_merged = 0
            self.last_missing_readings_recovered = 0
            self.current_missing_hourly_readings = len(missing)
            self.oldest_missing_hour = missing[0] if missing else None
            self.last_success_at = dt_util.now()
            self._set_source_status(self._source_status_for_readings(data.readings))
        except VsChrudimAuthError as err:
            self.last_test_download_result = "authentication_failed"
            self.last_test_download_error = "Authentication is no longer valid"
            self._set_source_status(
                "authentication_required", error=err, notify_problem=True
            )
            raise ConfigEntryAuthFailed from err
        except VsChrudimError as err:
            self.last_test_download_result = "failed"
            self.last_test_download_error = sanitize_error_message(err)
            self._set_source_status("error", error=err, notify_problem=True)
            raise HomeAssistantError(
                self.last_test_download_error or "The portal download test failed"
            ) from err
        finally:
            self.async_update_listeners()

    async def async_retry_history_download(self) -> None:
        """Retry the current portal download and reconcile available history.

        This deliberately does not clear or rebuild Energy statistics. Every
        returned reading is merged by timestamp and the statistics builders
        emit one external statistic per hour, so a repeated retry is safe.
        """
        if self.statistics_operation_running:
            raise HomeAssistantError(
                "Energy statistics maintenance is already in progress"
            )
        if self.history_backfill_running:
            raise HomeAssistantError("Water-meter history download is already running")

        # Make the live reading and "Data available through" diagnostic fresh
        # before scheduling the longer, resumable history reconciliation.
        await self.async_request_refresh()
        if self.last_attempt_result != "success":
            raise HomeAssistantError(
                self.last_attempt_error or "The portal data download failed"
            )

        # A successful regular update auto-resumes a paused/failed backfill.
        # Let that callback run first; it is the same task the button requests.
        await asyncio.sleep(0)
        if self.history_backfill_running:
            return
        if not self.async_start_history_backfill():
            raise HomeAssistantError("Water-meter history download could not start")

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
                self.history_backfill_imported_hours += await self._async_import_readings(
                    self._known_readings
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
