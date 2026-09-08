"""Coordinator for VSChrudim watermeter."""
from __future__ import annotations
import asyncio
from dataclasses import replace
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
from .statistics_health import (
    EnergyStatisticsHealth,
    MeterRegisterHealth,
    StatisticsImportResult,
    StatisticsState,
    assess_energy_statistics,
    assess_meter_register,
    energy_statistics_error,
)

_LOGGER = logging.getLogger(__name__)
_HISTORY_STORE_VERSION = 1
_ATTEMPT_STORE_VERSION = 1
_NOTIFICATION_STORE_VERSION = 1
_STATISTICS_STATE_STORE_VERSION = 1
_BACKFILL_REQUEST_DELAY = 2
_STATISTICS_OPERATION_TIMEOUT = 30
_MAX_FAILURE_RETRY_SECONDS = 60 * 60
_SOURCE_STATUSES = frozenset(
    {"unknown", "ok", "delayed_data", "authentication_required", "error"}
)


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


def _failure_retry_after(failures: int, configured_delay: int) -> int:
    """Return a bounded exponential retry delay for failed polling only.

    A successful download resets the failure count. Manual diagnostics invoke
    the client directly and are intentionally not delayed by this value.
    """
    base_delay = max(60, configured_delay)
    exponent = min(max(0, failures - 1), 6)
    return min(_MAX_FAILURE_RETRY_SECONDS, base_delay * (1 << exponent))

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
        self.statistics_write_status = "never"
        self.statistics_last_attempt_at: datetime | None = None
        self.statistics_last_success_at: datetime | None = None
        self.statistics_last_error_type: str | None = None
        self.statistics_last_error: str | None = None
        self.statistics_last_written_points = 0
        self.statistics_recovery_pending = False
        self.energy_statistics_health = EnergyStatisticsHealth()
        self.meter_register_health = MeterRegisterHealth()
        self.last_attempt_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_attempt_result = "never"
        self.last_attempt_error: str | None = None
        self.source_status = "unknown"
        self.last_download_source = "unknown"
        self.last_download_latest_timestamp: datetime | None = None
        self.last_download_reading_count = 0
        self.last_portal_page_features: tuple[str, ...] = ()
        self.last_reading_quality_flags: tuple[str, ...] = ()
        self.last_duplicate_readings_merged = 0
        self.last_missing_readings_recovered = 0
        self.current_missing_hourly_readings = 0
        self.oldest_missing_hour: datetime | None = None
        self.last_test_download_at: datetime | None = None
        self.last_test_download_result = "never"
        self.last_test_download_error: str | None = None
        self._source_problem_notification_active = False
        self._missing_problem_notification_active = False
        self._statistics_problem_notification_active = False
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
        self._notification_store: Store[dict[str, object]] = Store(
            hass,
            _NOTIFICATION_STORE_VERSION,
            f"{DOMAIN}.notification_state.{entry.entry_id}",
        )
        self._statistics_state_store: Store[dict[str, object]] = Store(
            hass,
            _STATISTICS_STATE_STORE_VERSION,
            f"{DOMAIN}.statistics_state.{entry.entry_id}",
        )
        self.download_attempt_history: list[DownloadAttempt] = []

    async def async_initialize(self) -> None:
        """Restore non-sensitive history progress after restart."""
        await self._async_load_notification_state()
        await self._async_load_statistics_state()
        try:
            attempts = await self._attempt_store.async_load()
        except Exception:  # pragma: no cover - backend storage varies by HA
            _LOGGER.warning("Could not restore download-attempt diagnostics")
            attempts = None
        self.download_attempt_history = load_attempt_history(attempts)
        try:
            stored = await self._history_store.async_load()
        except Exception:  # pragma: no cover - backend storage varies by HA
            _LOGGER.warning("Could not restore history-backfill progress")
            return
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

    def _statistics_state(self) -> StatisticsState:
        """Return a deliberately small checkpoint for external statistics."""
        return StatisticsState(
            status=self.statistics_write_status,
            last_attempt_at=self.statistics_last_attempt_at,
            last_success_at=self.statistics_last_success_at,
            last_error_type=self.statistics_last_error_type,
            last_error=self.statistics_last_error,
            last_written_points=self.statistics_last_written_points,
            recovery_pending=self.statistics_recovery_pending,
            health_status=self.energy_statistics_health.status,
            last_health_check_at=self.energy_statistics_health.checked_at,
        )

    def _apply_statistics_state(self, state: StatisticsState) -> None:
        """Apply only validated state restored from the dedicated Store."""
        self.statistics_write_status = state.status
        self.statistics_last_attempt_at = state.last_attempt_at
        self.statistics_last_success_at = state.last_success_at
        self.statistics_last_error_type = state.last_error_type
        self.statistics_last_error = state.last_error
        self.statistics_last_written_points = state.last_written_points
        self.statistics_recovery_pending = state.recovery_pending
        self.energy_statistics_health = EnergyStatisticsHealth(
            status=state.health_status,
            checked_at=state.last_health_check_at,
            write_pending=state.recovery_pending,
            error_type=state.last_error_type,
            error=state.last_error,
        )

    async def _async_load_statistics_state(self) -> None:
        """Restore a safe statistics checkpoint without retaining readings."""
        try:
            stored = await self._statistics_state_store.async_load()
        except Exception:  # pragma: no cover - backend storage varies by HA
            _LOGGER.warning("Could not restore Energy-statistics state")
            return
        self._apply_statistics_state(StatisticsState.from_dict(stored))

    async def _async_save_statistics_state(self) -> None:
        """Best-effort persistence that can never invalidate portal data."""
        try:
            await self._statistics_state_store.async_save(
                self._statistics_state().as_dict()
            )
        except Exception:  # pragma: no cover - backend storage varies by HA
            _LOGGER.warning("Could not save Energy-statistics state")

    async def _async_load_notification_state(self) -> None:
        """Restore only the safe notification-transition state."""
        try:
            stored = await self._notification_store.async_load()
        except Exception:  # pragma: no cover - storage failures are environment-specific
            # Notification deduplication must never stop the actual portal
            # update. The next successful state transition will establish a
            # fresh, safe transition state.
            _LOGGER.warning("Could not restore notification transition state")
            return
        if not isinstance(stored, dict):
            return
        source_status = stored.get("source_status")
        if isinstance(source_status, str) and source_status in _SOURCE_STATUSES:
            self.source_status = source_status
        source_problem_active = stored.get("source_problem_notification_active")
        if isinstance(source_problem_active, bool):
            self._source_problem_notification_active = source_problem_active
        missing_problem_active = stored.get("missing_problem_notification_active")
        if isinstance(missing_problem_active, bool):
            self._missing_problem_notification_active = missing_problem_active
        statistics_problem_active = stored.get(
            "statistics_problem_notification_active"
        )
        if isinstance(statistics_problem_active, bool):
            self._statistics_problem_notification_active = statistics_problem_active

    async def _async_save_notification_state(self) -> None:
        """Persist transition flags without customer or portal data."""
        try:
            await self._notification_store.async_save(
                {
                    "source_status": self.source_status,
                    "source_problem_notification_active": (
                        self._source_problem_notification_active
                    ),
                    "missing_problem_notification_active": (
                        self._missing_problem_notification_active
                    ),
                    "statistics_problem_notification_active": (
                        self._statistics_problem_notification_active
                    ),
                }
            )
        except Exception:  # pragma: no cover - storage failures are environment-specific
            _LOGGER.warning("Could not persist notification transition state")

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

    @property
    def _statistics_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_energy_statistics"

    @property
    def _statistics_recovered_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_energy_statistics_recovered"

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

    async def _set_source_status(
        self,
        status: str,
        *,
        error: Exception | None = None,
        notify_problem: bool = False,
    ) -> None:
        """Update source state and notify once per problem-state transition."""
        previous = self.source_status
        previous_problem_active = self._source_problem_notification_active
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
            if (
                previous != self.source_status
                or previous_problem_active != self._source_problem_notification_active
            ):
                await self._async_save_notification_state()
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
        if (
            previous != self.source_status
            or previous_problem_active != self._source_problem_notification_active
        ):
            await self._async_save_notification_state()

    async def _handle_missing_hours_notification(
        self, missing: tuple[datetime, ...], recovery_attempts: int
    ) -> None:
        """Notify only when missing readings appear or are fully recovered."""
        previous_problem_active = self._missing_problem_notification_active
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
            if previous_problem_active != self._missing_problem_notification_active:
                await self._async_save_notification_state()
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
        if previous_problem_active != self._missing_problem_notification_active:
            await self._async_save_notification_state()

    async def _handle_energy_statistics_notification(
        self, health: EnergyStatisticsHealth
    ) -> None:
        """Notify once when the integration-owned series needs attention.

        The message intentionally contains only a state and safe counters. It
        never exposes a statistic ID, portal response, customer details or a
        raw Recorder error.
        """
        previous_problem_active = self._statistics_problem_notification_active
        problem = health.status in {"pending", "incomplete", "error"}
        if problem:
            if not self._statistics_problem_notification_active:
                async_dismiss(self.hass, self._statistics_recovered_notification_id)
                message = (
                    "VSChrudim Energy statistics need attention. "
                    f"Status: {health.status}; missing consumption points: "
                    f"{health.missing_consumption_points}; missing cost points: "
                    f"{health.missing_cost_points}."
                )
                if health.error:
                    message += f" Last error: {health.error}"
                async_create(
                    self.hass,
                    message,
                    title="VSChrudim watermeter – Energy statistics pending",
                    notification_id=self._statistics_notification_id,
                )
                self._statistics_problem_notification_active = True
        else:
            async_dismiss(self.hass, self._statistics_notification_id)
            if health.status == "ok" and self._statistics_problem_notification_active:
                async_create(
                    self.hass,
                    "VSChrudim Energy statistics were verified successfully again.",
                    title="VSChrudim watermeter – Energy statistics restored",
                    notification_id=self._statistics_recovered_notification_id,
                )
                self._statistics_problem_notification_active = False
            elif health.status == "unknown":
                # No completed portal hour is not a problem and must not
                # create a stale alert on a fresh installation.
                self._statistics_problem_notification_active = False
        if previous_problem_active != self._statistics_problem_notification_active:
            await self._async_save_notification_state()

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
            # A normal measured-states response covers only the portal's
            # current reporting range. `_known_readings` additionally contains
            # historical backfill data, so looking for gaps in the merged
            # series would turn an old historical gap into a false current
            # outage notification. Recovery retries can only repair the range
            # returned by this current download.
            current_readings = data.readings
            initially_missing = find_missing_hours(current_readings)
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
                current_readings = merge_readings(current_readings, retry_data.readings)
                missing = find_missing_hours(current_readings)
            self._consecutive_failures = 0
            self.last_download_source = data.download_metadata.source
            self.last_download_latest_timestamp = (
                data.readings[-1].timestamp if data.readings else None
            )
            self.last_download_reading_count = len(data.readings)
            self.last_portal_page_features = (
                data.download_metadata.portal_page_features
            )
            self.last_reading_quality_flags = (
                data.download_metadata.reading_quality_flags
            )
            self.last_duplicate_readings_merged = duplicate_readings
            self.last_missing_readings_recovered = len(
                set(initially_missing) - set(missing)
            )
            self.current_missing_hourly_readings = len(missing)
            self.oldest_missing_hour = missing[0] if missing else None
            try:
                self.meter_register_health = assess_meter_register(
                    merged, checked_at=dt_util.now()
                )
            except Exception:  # pragma: no cover - defensive diagnostics guard
                _LOGGER.warning("Could not assess water-meter register health")
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
            await self._set_source_status(
                self._source_status_for_readings(data.readings)
            )
            await self._handle_missing_hours_notification(missing, attempts)
            await self._async_import_readings(merged)
            self._known_readings = merged
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
            await self._set_source_status(
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
            await self._set_source_status(
                "error",
                error=err,
                notify_problem=self._consecutive_failures >= threshold,
            )
            raise UpdateFailed(
                str(err),
                retry_after=_failure_retry_after(
                    self._consecutive_failures,
                    int(
                        self.entry.options.get(
                            CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY
                        )
                    ),
                ),
            ) from err
        except Exception as err:
            # Keep an unexpected integration-side failure isolated to this
            # coordinator update. Existing readings and external statistics
            # remain untouched, while the safe diagnostics still record it.
            _LOGGER.error(
                "Unexpected VSChrudim watermeter update failure (%s)",
                type(err).__name__,
            )
            safe_error = RuntimeError("Unexpected internal update error")
            self.last_attempt_result = "failed"
            self.last_attempt_error = str(safe_error)
            await self._async_record_download_attempt(
                started_at,
                started_monotonic,
                result="failed",
                error=safe_error,
            )
            self._consecutive_failures += 1
            threshold = int(
                self.entry.options.get(
                    CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD
                )
            )
            await self._set_source_status(
                "error",
                error=safe_error,
                notify_problem=self._consecutive_failures >= threshold,
            )
            raise UpdateFailed(
                "Unexpected VSChrudim watermeter update failure",
                retry_after=_failure_retry_after(
                    self._consecutive_failures,
                    int(
                        self.entry.options.get(
                            CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY
                        )
                    ),
                ),
            ) from err

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

    def _expected_energy_statistics(
        self,
        readings: tuple[MeterReading, ...],
        *,
        now: datetime,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        """Build exactly the completed portal points the writer would create."""
        local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
        consumption = meter_statistics(readings, local_tz=local_tz, now=now)
        cost = cost_statistics(
            readings,
            price_per_m3=float(
                self.entry.options.get(CONF_PRICE_PER_M3, DEFAULT_PRICE_PER_M3)
            ),
            local_tz=local_tz,
            now=now,
        )
        # StatisticData is a TypedDict at runtime. The explicit conversion
        # keeps the read-only health helper independent of Home Assistant.
        return [dict(row) for row in consumption], [dict(row) for row in cost]

    def _statistics_readings(
        self, readings: tuple[MeterReading, ...] | None
    ) -> tuple[MeterReading, ...]:
        """Choose only already validated in-memory readings for a check."""
        if readings is not None:
            return readings
        if self._known_readings:
            return self._known_readings
        data = self.data
        return data.readings if data else ()

    async def _async_read_energy_statistics(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        """Read the two integration-owned external series without mutation."""
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start or dt_util.utc_from_timestamp(0),
            end,
            {self.consumption_statistic_id, self.cost_statistic_id},
            "hour",
            None,
            {"state", "sum"},
        )
        if not isinstance(rows, dict):
            raise HomeAssistantError("Recorder returned an invalid statistics result")

        def valid_rows(value: object) -> list[dict[str, object]]:
            if not isinstance(value, list):
                return []
            return [dict(item) for item in value if isinstance(item, dict)]

        return (
            valid_rows(rows.get(self.consumption_statistic_id)),
            valid_rows(rows.get(self.cost_statistic_id)),
        )

    async def _async_verify_statistics_points(
        self, readings: tuple[MeterReading, ...]
    ) -> bool:
        """Verify one backfill block before its source cursor advances.

        This intentionally reads only the block's bounded statistic interval.
        A full diagnostic health scan still runs once at backfill completion,
        while every individual checkpoint remains protected from a failed
        Recorder write without repeatedly querying a growing three-year range.
        """
        checked_at = dt_util.now()
        try:
            expected_consumption, expected_cost = self._expected_energy_statistics(
                readings, now=checked_at
            )
            expected_starts = [
                start
                for row in [*expected_consumption, *expected_cost]
                if isinstance((start := row.get("start")), datetime)
            ]
            if not expected_starts:
                return True
            start = min(expected_starts)
            end = max(expected_starts) + timedelta(hours=1)
            health: EnergyStatisticsHealth | None = None
            for verification_attempt in range(6):
                consumption_rows, cost_rows = await self._async_read_energy_statistics(
                    start=start, end=end
                )
                health = assess_energy_statistics(
                    expected_consumption_rows=expected_consumption,
                    expected_cost_rows=expected_cost,
                    consumption_rows=consumption_rows,
                    cost_rows=cost_rows,
                    portal_latest_timestamp=readings[-1].timestamp,
                    write_pending=False,
                    checked_at=checked_at,
                )
                if health.status == "ok":
                    return True
                if verification_attempt < 5:
                    await asyncio.sleep(0.2)
            assert health is not None
        except asyncio.CancelledError:
            raise
        except Exception as err:  # pragma: no cover - Recorder backend errors vary
            _LOGGER.warning(
                "Could not verify backfill Energy statistics (%s)",
                type(err).__name__,
            )
            self.statistics_write_status = "error"
            self.statistics_recovery_pending = True
            self.statistics_last_error_type = type(err).__name__
            self.statistics_last_error = sanitize_error_message(err)
            await self._async_publish_energy_statistics_health(
                energy_statistics_error(
                    err, checked_at=checked_at, write_pending=True
                ),
                update_listeners=False,
            )
            return False

        self.statistics_write_status = "pending"
        self.statistics_recovery_pending = True
        health = replace(health, write_pending=True)
        await self._async_publish_energy_statistics_health(
            health, update_listeners=False
        )
        return False

    async def _async_publish_energy_statistics_health(
        self,
        health: EnergyStatisticsHealth,
        *,
        update_listeners: bool,
    ) -> None:
        """Persist and publish auxiliary state without risking portal data."""
        self.energy_statistics_health = health
        await self._async_save_statistics_state()
        try:
            await self._handle_energy_statistics_notification(health)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - notification backends vary
            _LOGGER.warning("Could not update Energy-statistics notification")
        if update_listeners:
            try:
                self.async_update_listeners()
            except Exception:  # pragma: no cover - listener failures are external
                _LOGGER.warning("Could not publish Energy-statistics diagnostics")

    async def async_check_energy_statistics(
        self,
        readings: tuple[MeterReading, ...] | None = None,
        *,
        _allow_during_operation: bool = False,
        _verification_retries: int = 0,
        update_listeners: bool = True,
    ) -> EnergyStatisticsHealth:
        """Read and compare Energy statistics without contacting the portal.

        This method intentionally has no write, delete, backfill or sensor-data
        side effect. It uses only the coordinator's already validated readings
        and the two integration-owned Recorder series.
        """
        if self.statistics_operation_running and not _allow_during_operation:
            raise HomeAssistantError("Energy statistics maintenance is in progress")

        known_readings = self._statistics_readings(readings)
        checked_at = dt_util.now()
        try:
            expected_consumption, expected_cost = self._expected_energy_statistics(
                known_readings, now=checked_at
            )
            for verification_attempt in range(max(0, _verification_retries) + 1):
                consumption_rows, cost_rows = await self._async_read_energy_statistics()
                health = assess_energy_statistics(
                    expected_consumption_rows=expected_consumption,
                    expected_cost_rows=expected_cost,
                    consumption_rows=consumption_rows,
                    cost_rows=cost_rows,
                    portal_latest_timestamp=(
                        known_readings[-1].timestamp if known_readings else None
                    ),
                    write_pending=False,
                    checked_at=checked_at,
                )
                if (
                    health.status != "incomplete"
                    or verification_attempt >= max(0, _verification_retries)
                ):
                    break
                # Recorder accepts external statistics asynchronously. A short
                # bounded wait avoids classifying a just-accepted normal write
                # as missing before Recorder has committed it.
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # pragma: no cover - Recorder backend errors vary
            _LOGGER.warning("Could not check Energy statistics (%s)", type(err).__name__)
            self.statistics_write_status = "error"
            self.statistics_recovery_pending = True
            self.statistics_last_error_type = type(err).__name__
            self.statistics_last_error = sanitize_error_message(err)
            health = energy_statistics_error(
                err,
                checked_at=checked_at,
                write_pending=True,
            )
            await self._async_publish_energy_statistics_health(
                health, update_listeners=update_listeners
            )
            return health

        if health.status == "ok":
            self.statistics_write_status = "ok"
            self.statistics_recovery_pending = False
            self.statistics_last_error_type = None
            self.statistics_last_error = None
        elif health.status == "incomplete":
            # A mismatch is not a portal failure. Keep the verified portal
            # update, checkpoint a retry for a later successful update, and
            # leave all existing statistics untouched.
            self.statistics_write_status = "pending"
            self.statistics_recovery_pending = True
            health = replace(health, write_pending=True)
        elif health.status == "unknown" and self.statistics_recovery_pending:
            health = replace(health, write_pending=True)

        await self._async_publish_energy_statistics_health(
            health, update_listeners=update_listeners
        )
        return health

    async def _async_import_readings(
        self,
        readings: tuple[MeterReading, ...],
        *,
        verification_readings: tuple[MeterReading, ...] | None = None,
    ) -> StatisticsImportResult:
        """Queue completed external statistics without invalidating live data."""
        if self._statistics_writes_paused:
            return StatisticsImportResult(
                accepted=False,
                error_type="StatisticsWritesPaused",
                error="Energy statistics writes are paused",
            )
        if not readings:
            return StatisticsImportResult(accepted=True)

        attempted_at = dt_util.now()
        self.statistics_last_attempt_at = attempted_at
        try:
            local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
            initial_sum, initial_meter_state = await self._async_statistics_seed(
                readings, local_tz, attempted_at
            )
            consumption_points = async_add_external_meter_statistics(
                self.hass,
                statistic_id=self.consumption_statistic_id,
                readings=readings,
                local_tz=local_tz,
                now=attempted_at,
                initial_sum=initial_sum,
                initial_meter_state=initial_meter_state,
            )
            cost_points = async_add_external_cost_statistics(
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
                now=attempted_at,
                initial_sum=initial_sum,
                initial_meter_state=initial_meter_state,
            )
            if consumption_points != cost_points:
                raise HomeAssistantError("Energy statistics point counts did not match")
        except asyncio.CancelledError:
            raise
        except Exception as err:  # pragma: no cover - Recorder backend errors vary
            # Statistics are auxiliary to a verified portal download. Do not
            # discard the live readings or clear any existing series when the
            # Recorder write is temporarily unavailable.
            _LOGGER.error("Could not import water-meter history (%s)", type(err).__name__)
            self.statistics_write_status = "error"
            self.statistics_recovery_pending = True
            self.statistics_last_error_type = type(err).__name__
            self.statistics_last_error = sanitize_error_message(err)
            health = energy_statistics_error(
                err,
                checked_at=attempted_at,
                write_pending=True,
            )
            await self._async_publish_energy_statistics_health(
                health, update_listeners=False
            )
            return StatisticsImportResult(
                accepted=False,
                error_type=type(err).__name__,
                error=sanitize_error_message(err),
            )

        self.statistics_last_success_at = attempted_at
        self.statistics_last_written_points = consumption_points
        self.statistics_last_error_type = None
        self.statistics_last_error = None
        if consumption_points:
            # The write is idempotent, but remains pending until the read-only
            # Recorder comparison verifies both external series.
            self.statistics_write_status = "pending"
            self.statistics_recovery_pending = True
        else:
            self.statistics_write_status = "ok"
            self.statistics_recovery_pending = False
        await self._async_save_statistics_state()

        # A normal update gets a complete read-only health snapshot. During a
        # history scan, verify only the block about to be checkpointed; this
        # avoids repeatedly reading the entire growing three-year series while
        # still refusing to advance that block after a failed writer result.
        if verification_readings is None:
            await self.async_check_energy_statistics(
                readings, _verification_retries=5, update_listeners=False
            )
        elif not await self._async_verify_statistics_points(verification_readings):
            return StatisticsImportResult(
                accepted=False,
                written_points=consumption_points,
                error_type="StatisticsVerificationPending",
                error="Energy statistics write was not verified",
            )
        return StatisticsImportResult(
            accepted=True, written_points=consumption_points
        )

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
            portal_page_features=metadata.portal_page_features,
            reading_quality_flags=metadata.reading_quality_flags,
        )
        self.download_attempt_history = append_attempt(
            self.download_attempt_history, attempt
        )
        try:
            await self._attempt_store.async_save(
                [item.as_dict() for item in self.download_attempt_history]
            )
        except Exception:  # pragma: no cover - backend storage varies by HA
            # The diagnostics store is deliberately best effort. It must not
            # turn a completed source download into a failed update.
            _LOGGER.warning("Could not save download-attempt diagnostics")

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
            try:
                await self._async_clear_energy_statistics()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self.statistics_write_status = "error"
                self.statistics_recovery_pending = True
                self.statistics_last_error_type = type(err).__name__
                self.statistics_last_error = sanitize_error_message(err)
                await self._async_publish_energy_statistics_health(
                    energy_statistics_error(
                        err,
                        checked_at=dt_util.now(),
                        write_pending=True,
                    ),
                    update_listeners=True,
                )
                raise
            self.statistics_write_status = "pending"
            self.statistics_recovery_pending = False
            self.statistics_last_error_type = None
            self.statistics_last_error = None
            await self._async_publish_energy_statistics_health(
                EnergyStatisticsHealth(
                    status="pending",
                    checked_at=dt_util.now(),
                    write_pending=False,
                ),
                update_listeners=True,
            )

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
        readings: tuple[MeterReading, ...]
        async with self._statistics_operation_lock:
            # This deliberate preflight happens before the destructive clear.
            # If the portal is unavailable or its data is malformed, existing
            # Energy statistics remain untouched.
            readings = await self._async_fetch_complete_source_data()
            self._statistics_writes_paused = True
            self.statistics_ready = False
            attempted_at = dt_util.now()
            self.statistics_last_attempt_at = attempted_at
            try:
                await self._async_clear_energy_statistics()
                local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
                consumption_points = async_add_external_meter_statistics(
                    self.hass,
                    statistic_id=self.consumption_statistic_id,
                    readings=readings,
                    local_tz=local_tz,
                    now=attempted_at,
                )
                cost_points = async_add_external_cost_statistics(
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
                    now=attempted_at,
                )
                if consumption_points != cost_points:
                    raise HomeAssistantError(
                        "Energy statistics point counts did not match"
                    )
                await self._async_verify_energy_statistics(readings)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                # A user explicitly chose the destructive operation. Keep
                # ordinary live sensor history untouched and leave automatic
                # writes paused so an incomplete manual rebuild is never
                # silently mixed with a later incremental update.
                self.statistics_write_status = "error"
                self.statistics_recovery_pending = True
                self.statistics_last_error_type = type(err).__name__
                self.statistics_last_error = sanitize_error_message(err)
                await self._async_publish_energy_statistics_health(
                    energy_statistics_error(
                        err,
                        checked_at=dt_util.now(),
                        write_pending=True,
                    ),
                    update_listeners=True,
                )
                raise
            self._known_readings = merge_readings(self._known_readings, readings)
            self.statistics_last_success_at = attempted_at
            self.statistics_last_written_points = consumption_points
            self.statistics_write_status = "ok"
            self.statistics_recovery_pending = False
            self.statistics_last_error_type = None
            self.statistics_last_error = None
            self.statistics_ready = True
            self._statistics_writes_paused = False
            await self._async_save_statistics_state()

        # The rebuild verification above is the safety gate. A second normal
        # read-only health snapshot fills the diagnostic counters and may
        # never undo the completed rebuild.
        await self.async_check_energy_statistics(readings)

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

    async def async_shutdown(self) -> None:
        """Checkpoint and stop history work before an integration unload."""
        task = self._history_backfill_task
        if not task or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover - defensive unload protection
            _LOGGER.exception("History backfill stopped with an unload error")
        finally:
            self._history_backfill_task = None

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
            self.last_portal_page_features = (
                data.download_metadata.portal_page_features
            )
            self.last_reading_quality_flags = (
                data.download_metadata.reading_quality_flags
            )
            self.last_duplicate_readings_merged = 0
            self.last_missing_readings_recovered = 0
            self.current_missing_hourly_readings = len(missing)
            self.oldest_missing_hour = missing[0] if missing else None
            self.last_success_at = dt_util.now()
            await self._set_source_status(
                self._source_status_for_readings(data.readings)
            )
        except VsChrudimAuthError as err:
            self.last_test_download_result = "authentication_failed"
            self.last_test_download_error = "Authentication is no longer valid"
            await self._set_source_status(
                "authentication_required", error=err, notify_problem=True
            )
            raise ConfigEntryAuthFailed from err
        except VsChrudimError as err:
            self.last_test_download_result = "failed"
            self.last_test_download_error = sanitize_error_message(err)
            await self._set_source_status(
                "error", error=err, notify_problem=True
            )
            raise HomeAssistantError(
                self.last_test_download_error or "The portal download test failed"
            ) from err
        except Exception as err:
            _LOGGER.error(
                "Unexpected VSChrudim watermeter test-download failure (%s)",
                type(err).__name__,
            )
            safe_error = RuntimeError("Unexpected internal download-test error")
            self.last_test_download_result = "failed"
            self.last_test_download_error = str(safe_error)
            await self._set_source_status(
                "error", error=safe_error, notify_problem=True
            )
            raise HomeAssistantError("Unexpected portal download test failure") from err
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
        if self._statistics_writes_paused:
            # An explicitly cleared or interrupted destructive maintenance
            # operation leaves the writer paused by design. Do not fetch and
            # then repeatedly fail history blocks until the user rebuilds it.
            return False
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
                statistics_result = await self._async_import_readings(
                    self._known_readings,
                    verification_readings=readings,
                )
                if not statistics_result.accepted:
                    # Do not advance the source cursor for a block whose
                    # external statistic write was not accepted. The next
                    # successful portal update will resume the same safe,
                    # idempotent block without deleting anything.
                    raise HomeAssistantError(
                        statistics_result.error
                        or "Energy statistics write is pending"
                    )
                self.history_backfill_imported_hours += (
                    statistics_result.written_points
                )
                try:
                    self.meter_register_health = assess_meter_register(
                        self._known_readings, checked_at=dt_util.now()
                    )
                except Exception:  # pragma: no cover - defensive diagnostics guard
                    _LOGGER.warning("Could not assess water-meter register health")
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
        if not self.statistics_operation_running:
            # Finish with one complete read-only coverage snapshot. Individual
            # blocks were already verified before their cursors advanced.
            await self.async_check_energy_statistics(update_listeners=False)
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
