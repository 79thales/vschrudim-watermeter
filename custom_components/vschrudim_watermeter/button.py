"""Maintenance buttons for VSChrudim watermeter."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VsChrudimCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[VsChrudimCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the confirmed Energy-statistics rebuild button."""
    async_add_entities([RebuildEnergyStatisticsButton(entry.runtime_data)])


class RebuildEnergyStatisticsButton(ButtonEntity):
    """Rebuild integration-owned Energy statistics after source preflight."""

    _attr_has_entity_name = True
    _attr_translation_key = "rebuild_energy_statistics"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:database-sync"

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        self.coordinator = coordinator
        identifier = coordinator.place.identifier
        self._attr_unique_id = f"{identifier}_rebuild_energy_statistics"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=coordinator.place.address or f"VS Chrudim {identifier}",
            manufacturer="Vodárenská společnost Chrudim",
            model="Smart water meter",
        )

    @property
    def available(self) -> bool:
        """Disable another press while a maintenance operation is running."""
        return not self.coordinator.statistics_operation_running

    async def async_press(self) -> None:
        """Run the validated rebuild after an explicit user button press."""
        await self.coordinator.async_rebuild_energy_statistics(confirm=True)
