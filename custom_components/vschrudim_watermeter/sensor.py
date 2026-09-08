"""Sensors for VSChrudim watermeter."""
from __future__ import annotations
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from .calculation import total_cost
from .const import (
    CONF_PRICE_PER_M3,
    CONF_SOURCE_DELAY_WARNING_HOURS,
    DEFAULT_PRICE_PER_M3,
    DEFAULT_SOURCE_DELAY_WARNING_HOURS,
    DOMAIN,
)
from .coordinator import VsChrudimCoordinator

_DOWNLOAD_METHOD_LABELS = {
    "csv_link": "CSV link",
    "csv_postback": "WebForms postback",
    "csv_submit": "WebForms submit",
    "html_table": "HTML table",
    "unknown": "Unknown",
}

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry[VsChrudimCoordinator], async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities(
        [
            WaterMeterStateSensor(entry.runtime_data),
            LatestConsumptionSensor(entry.runtime_data),
            WaterPriceSensor(entry.runtime_data, entry),
            TotalWaterCostSensor(entry.runtime_data, entry),
            SourceStatusSensor(entry.runtime_data),
            DataAvailableThroughSensor(entry.runtime_data),
            LatestPortalReadingSensor(entry.runtime_data),
            MissingHourlyReadingsSensor(entry.runtime_data),
            OldestMissingHourlyReadingSensor(entry.runtime_data),
            LastDownloadReadingCountSensor(entry.runtime_data),
            DuplicateReadingsMergedSensor(entry.runtime_data),
            MissingReadingsRecoveredSensor(entry.runtime_data),
            LastUpdateAttemptSensor(entry.runtime_data),
            HistoryBackfillStatusSensor(entry.runtime_data),
        ]
    )

class _BaseSensor(CoordinatorEntity[VsChrudimCoordinator], SensorEntity):
    _attr_has_entity_name = True
    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        identifier = coordinator.place.identifier
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=coordinator.place.address or f"VS Chrudim {identifier}",
            manufacturer="Vodárenská společnost Chrudim",
            model="Smart water meter",
        )

    @property
    def available(self) -> bool:
        """Keep the last successful reading available during transient failures."""
        return self.coordinator.data is not None

class WaterMeterStateSensor(_BaseSensor):
    _attr_translation_key = "meter_state"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    # This is a live display value, not the owner of Energy statistics.
    # Recorder must not generate a competing sensor.* sum series; the
    # coordinator writes the single integration-owned external statistic.
    _attr_state_class = None
    _attr_suggested_display_precision = 3
    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.place.identifier}_meter_state"
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.async_register_meter_entity(self.entity_id)
    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return data.readings[-1].meter_state_m3 if data and data.readings else None
    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        data = self.coordinator.data
        if not data or not data.readings:
            return None
        latest = data.readings[-1]
        return {
            "last_reading": latest.timestamp.isoformat(),
            "meter": latest.meter,
            "missing_hourly_readings": len(data.missing_timestamps),
            "recovery_attempts": data.recovery_attempts,
        }

class LatestConsumptionSensor(_BaseSensor):
    _attr_translation_key = "latest_consumption"
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.place.identifier}_latest_consumption"
    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return data.latest_consumption_m3 if data else None

class WaterPriceSensor(_BaseSensor):
    """Configured all-in water price suitable for Energy dashboard cost tracking."""
    _attr_translation_key = "water_price"
    _attr_native_unit_of_measurement = "CZK/m³"
    _attr_state_class = SensorStateClass.MEASUREMENT
    def __init__(self, coordinator: VsChrudimCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.place.identifier}_water_price"
    @property
    def native_value(self) -> float:
        return float(self._entry.options.get(CONF_PRICE_PER_M3, DEFAULT_PRICE_PER_M3))
    @property
    def available(self) -> bool:
        """The configured local price does not depend on portal availability."""
        return True


class TotalWaterCostSensor(_BaseSensor):
    """Cumulative cost whose hourly history is imported with meter history."""

    _attr_translation_key = "total_water_cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "CZK"
    # Keep the entity available for display, but do not create another
    # Recorder sum series alongside the integration-owned external cost ID.
    _attr_state_class = None
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: VsChrudimCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.place.identifier}_total_water_cost"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.async_register_cost_entity(self.entity_id)

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data or not data.readings:
            return None
        price = float(
            self._entry.options.get(CONF_PRICE_PER_M3, DEFAULT_PRICE_PER_M3)
        )
        return total_cost(data.readings[-1].meter_state_m3, price)


class SourceStatusSensor(_BaseSensor):
    """One concise diagnostic state for portal reachability and freshness."""

    _attr_translation_key = "source_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.place.identifier}_source_status"

    @property
    def native_value(self) -> str:
        return self.coordinator.source_status

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        source = self.coordinator.last_download_source
        return {
            "last_success_at": self.coordinator.last_success_at.isoformat()
            if self.coordinator.last_success_at
            else None,
            "download_source": source,
            "download_method": _DOWNLOAD_METHOD_LABELS.get(source, "Unknown"),
            "latest_portal_timestamp": self.coordinator.last_download_latest_timestamp.isoformat()
            if self.coordinator.last_download_latest_timestamp
            else None,
            "delay_warning_hours": self.coordinator.entry.options.get(
                CONF_SOURCE_DELAY_WARNING_HOURS,
                DEFAULT_SOURCE_DELAY_WARNING_HOURS,
            ),
            "last_test_at": self.coordinator.last_test_download_at.isoformat()
            if self.coordinator.last_test_download_at
            else None,
            "last_test_result": self.coordinator.last_test_download_result,
            "last_test_error": self.coordinator.last_test_download_error,
        }

    @property
    def available(self) -> bool:
        return True


class DataAvailableThroughSensor(_BaseSensor):
    """Newest portal timestamp contained in the successful download."""

    _attr_translation_key = "data_available_through"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.place.identifier}_data_available_through"

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data or not data.readings:
            return None
        local_tz = dt_util.get_time_zone(self.coordinator.hass.config.time_zone)
        return data.readings[-1].timestamp.replace(tzinfo=local_tz)

    @property
    def available(self) -> bool:
        return True


class LatestPortalReadingSensor(_BaseSensor):
    """Exact local timestamp of the newest reading returned by the portal."""

    _attr_translation_key = "latest_portal_reading"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.place.identifier}_latest_portal_reading"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if not data or not data.readings:
            return None
        # A plain text state is intentional. Home Assistant renders timestamp
        # entities relatively (for example "21 hours ago"), whereas this
        # diagnostic must show the exact local portal reading time directly.
        return data.readings[-1].timestamp.strftime("%d.%m.%Y %H:%M")

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        data = self.coordinator.data
        if not data or not data.readings:
            return None
        local_tz = dt_util.get_time_zone(self.coordinator.hass.config.time_zone)
        return {
            "timestamp": data.readings[-1].timestamp.replace(
                tzinfo=local_tz
            ).isoformat()
        }

    @property
    def available(self) -> bool:
        return True


class MissingHourlyReadingsSensor(_BaseSensor):
    """Number of currently detected internal hourly gaps."""

    _attr_translation_key = "missing_hourly_readings"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.place.identifier}_missing_hourly_readings"

    @property
    def native_value(self) -> int:
        return self.coordinator.current_missing_hourly_readings

    @property
    def available(self) -> bool:
        return True


class OldestMissingHourlyReadingSensor(_BaseSensor):
    """Oldest internal hourly gap detected in the current readings."""

    _attr_translation_key = "oldest_missing_hourly_reading"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{coordinator.place.identifier}_oldest_missing_hourly_reading"
        )

    @property
    def native_value(self):
        if self.coordinator.oldest_missing_hour is None:
            return None
        local_tz = dt_util.get_time_zone(self.coordinator.hass.config.time_zone)
        return self.coordinator.oldest_missing_hour.replace(tzinfo=local_tz)

    @property
    def available(self) -> bool:
        return True


class LastDownloadReadingCountSensor(_BaseSensor):
    """Number of readings returned by the most recent portal download."""

    _attr_translation_key = "last_download_reading_count"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{coordinator.place.identifier}_last_download_reading_count"
        )

    @property
    def native_value(self) -> int:
        return self.coordinator.last_download_reading_count

    @property
    def available(self) -> bool:
        return True


class DuplicateReadingsMergedSensor(_BaseSensor):
    """Timestamp collisions collapsed during the most recent download."""

    _attr_translation_key = "duplicate_readings_merged"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{coordinator.place.identifier}_duplicate_readings_merged"
        )

    @property
    def native_value(self) -> int:
        return self.coordinator.last_duplicate_readings_merged

    @property
    def available(self) -> bool:
        return True


class MissingReadingsRecoveredSensor(_BaseSensor):
    """Gaps filled by recovery downloads during the most recent update."""

    _attr_translation_key = "missing_readings_recovered"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{coordinator.place.identifier}_missing_readings_recovered"
        )

    @property
    def native_value(self) -> int:
        return self.coordinator.last_missing_readings_recovered

    @property
    def available(self) -> bool:
        return True


class LastUpdateAttemptSensor(_BaseSensor):
    """Timestamp and outcome of the most recent coordinator attempt."""

    _attr_translation_key = "last_update_attempt"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.place.identifier}_last_update_attempt"

    @property
    def native_value(self):
        return self.coordinator.last_attempt_at

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "result": self.coordinator.last_attempt_result,
            "last_success": self.coordinator.last_success_at.isoformat()
            if self.coordinator.last_success_at
            else None,
            "last_error": self.coordinator.last_attempt_error,
        }


class HistoryBackfillStatusSensor(_BaseSensor):
    """Resumable three-year backfill progress without customer identifiers."""

    _attr_translation_key = "history_backfill_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.place.identifier}_history_backfill_status"

    @property
    def native_value(self) -> str:
        return self.coordinator.history_backfill_status

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "processed_chunks": self.coordinator.history_backfill_processed_chunks,
            "total_chunks": self.coordinator.history_backfill_total_chunks,
            "imported_hours": self.coordinator.history_backfill_imported_hours,
            "scan_start": self.coordinator.history_backfill_scan_start.isoformat()
            if self.coordinator.history_backfill_scan_start
            else None,
            "next_range_end": self.coordinator.history_backfill_cursor.isoformat()
            if self.coordinator.history_backfill_cursor
            else None,
            "earliest_data": self.coordinator.history_earliest_date.isoformat()
            if self.coordinator.history_earliest_date
            else None,
            "started_at": self.coordinator.history_backfill_started_at.isoformat()
            if self.coordinator.history_backfill_started_at
            else None,
            "completed_at": self.coordinator.history_backfill_completed_at.isoformat()
            if self.coordinator.history_backfill_completed_at
            else None,
            "last_error": self.coordinator.history_backfill_error,
        }
