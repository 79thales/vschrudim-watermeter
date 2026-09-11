"""Maintenance buttons for VSChrudim watermeter."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VsChrudimCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[VsChrudimCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up independent data-download and statistics-maintenance buttons."""
    async_add_entities(
        [
            TestDownloadButton(entry.runtime_data),
            RetryHistoryDownloadButton(entry.runtime_data),
            CheckEnergyStatisticsButton(entry.runtime_data),
            RebuildEnergyStatisticsButton(entry.runtime_data),
        ]
    )


class RetryHistoryDownloadButton(
    CoordinatorEntity[VsChrudimCoordinator], ButtonEntity
):
    """Request an immediate, idempotent retry of portal data download."""

    _attr_has_entity_name = True
    _attr_translation_key = "retry_history_download"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-download-outline"

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        identifier = coordinator.place.identifier
        self._attr_unique_id = f"{identifier}_retry_history_download"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=coordinator.place.address or f"VS Chrudim {identifier}",
            manufacturer="Vodárenská společnost Chrudim",
            model="Smart water meter",
        )

    @property
    def available(self) -> bool:
        """Do not overlap history transfer or destructive statistics work."""
        return (
            not self.coordinator.history_backfill_running
            and not self.coordinator.statistics_operation_running
        )

    async def async_press(self) -> None:
        """Download current data, then resume or restart history reconciliation."""
        await self.coordinator.async_retry_history_download()


class TestDownloadButton(CoordinatorEntity[VsChrudimCoordinator], ButtonEntity):
    """Check authentication and a current portal download without side effects."""

    _attr_has_entity_name = True
    _attr_translation_key = "test_download"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:connection"

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        identifier = coordinator.place.identifier
        self._attr_unique_id = f"{identifier}_test_download"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=coordinator.place.address or f"VS Chrudim {identifier}",
            manufacturer="Vodárenská společnost Chrudim",
            model="Smart water meter",
        )

    @property
    def available(self) -> bool:
        return not self.coordinator.statistics_operation_running

    async def async_press(self) -> None:
        await self.coordinator.async_test_download()


class RebuildEnergyStatisticsButton(
    CoordinatorEntity[VsChrudimCoordinator], ButtonEntity
):
    """Rebuild integration-owned Energy statistics after source preflight."""

    _attr_has_entity_name = True
    _attr_translation_key = "rebuild_energy_statistics"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:database-sync"

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
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


class CheckEnergyStatisticsButton(
    CoordinatorEntity[VsChrudimCoordinator], ButtonEntity
):
    """Read-only comparison of portal-derived and Recorder statistics."""

    _attr_has_entity_name = True
    _attr_translation_key = "check_energy_statistics"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:database-check-outline"

    def __init__(self, coordinator: VsChrudimCoordinator) -> None:
        super().__init__(coordinator)
        identifier = coordinator.place.identifier
        self._attr_unique_id = f"{identifier}_check_energy_statistics"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=coordinator.place.address or f"VS Chrudim {identifier}",
            manufacturer="Vodárenská společnost Chrudim",
            model="Smart water meter",
        )

    @property
    def available(self) -> bool:
        """Only a destructive clear/rebuild temporarily blocks this check."""
        return not self.coordinator.statistics_operation_running

    async def async_press(self) -> None:
        """Check the current in-memory source snapshot without portal I/O."""
        await self.coordinator.async_check_energy_statistics()
