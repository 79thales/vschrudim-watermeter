"""Sensors for VSChrudim watermeter."""
from __future__ import annotations
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN
from .coordinator import VsChrudimCoordinator

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry[VsChrudimCoordinator], async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([WaterMeterStateSensor(entry.runtime_data), LatestConsumptionSensor(entry.runtime_data)])

class _BaseSensor(CoordinatorEntity[VsChrudimCoordinator], SensorEntity):
    _attr_has_entity_name = True
    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        identifier = coordinator.place.identifier
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, identifier)}, name=coordinator.place.address or f"VS Chrudim {identifier}", manufacturer="Vodárenská společnost Chrudim", model="Smart water meter")

class WaterMeterStateSensor(_BaseSensor):
    _attr_translation_key = "meter_state"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.place.identifier}_meter_state"
    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.readings[-1].meter_state_m3 if self.coordinator.data.readings else None
    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if not self.coordinator.data.readings:
            return None
        latest = self.coordinator.data.readings[-1]
        return {"last_reading": latest.timestamp.isoformat(), "meter": latest.meter}

class LatestConsumptionSensor(_BaseSensor):
    _attr_translation_key = "latest_consumption"
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.place.identifier}_latest_consumption"
    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.latest_consumption_m3
