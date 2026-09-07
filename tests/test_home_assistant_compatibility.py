"""Smoke tests run in CI with supported Home Assistant releases."""
from __future__ import annotations

import importlib
import importlib.util
import unittest

HOME_ASSISTANT_INSTALLED = importlib.util.find_spec("homeassistant") is not None


@unittest.skipUnless(HOME_ASSISTANT_INSTALLED, "Home Assistant is installed only in compatibility CI")
class HomeAssistantCompatibilityTests(unittest.TestCase):
    def test_all_integration_modules_import(self):
        for module in (
            "custom_components.vschrudim_watermeter",
            "custom_components.vschrudim_watermeter.api",
            "custom_components.vschrudim_watermeter.calculation",
            "custom_components.vschrudim_watermeter.config_flow",
            "custom_components.vschrudim_watermeter.coordinator",
            "custom_components.vschrudim_watermeter.diagnostics",
            "custom_components.vschrudim_watermeter.history",
            "custom_components.vschrudim_watermeter.models",
            "custom_components.vschrudim_watermeter.recovery",
            "custom_components.vschrudim_watermeter.sensor",
        ):
            with self.subTest(module=module):
                importlib.import_module(module)

    def test_water_sensor_contract(self):
        from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
        from homeassistant.const import UnitOfVolume
        from custom_components.vschrudim_watermeter.sensor import WaterMeterStateSensor

        sensor = object.__new__(WaterMeterStateSensor)
        self.assertEqual(sensor.device_class, SensorDeviceClass.WATER)
        self.assertEqual(sensor.state_class, SensorStateClass.TOTAL_INCREASING)
        self.assertEqual(sensor.native_unit_of_measurement, UnitOfVolume.CUBIC_METERS)

        from custom_components.vschrudim_watermeter.sensor import TotalWaterCostSensor

        cost_sensor = object.__new__(TotalWaterCostSensor)
        self.assertEqual(cost_sensor.device_class, SensorDeviceClass.MONETARY)
        self.assertEqual(cost_sensor.state_class, SensorStateClass.TOTAL)
        self.assertEqual(cost_sensor.native_unit_of_measurement, "CZK")

    def test_history_is_imported_under_real_meter_entity(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from custom_components.vschrudim_watermeter.history import meter_statistics
        from custom_components.vschrudim_watermeter.models import MeterReading

        rows = meter_statistics(
            (
                MeterReading(datetime(2026, 1, 1, 10), 100.0),
                MeterReading(datetime(2026, 1, 1, 11), 100.125),
            ),
            local_tz=ZoneInfo("Europe/Prague"),
            now=datetime(2026, 1, 1, 13, tzinfo=ZoneInfo("Europe/Prague")),
        )
        self.assertEqual([row["sum"] for row in rows], [100.0, 100.125])
        self.assertTrue(all(row["start"].tzinfo is not None for row in rows))

        from custom_components.vschrudim_watermeter.history import cost_statistics

        cost_rows = cost_statistics(
            (
                MeterReading(datetime(2026, 1, 1, 10), 100.0),
                MeterReading(datetime(2026, 1, 1, 11), 100.125),
            ),
            price_per_m3=120.0,
            local_tz=ZoneInfo("Europe/Prague"),
            now=datetime(2026, 1, 1, 13, tzinfo=ZoneInfo("Europe/Prague")),
        )
        self.assertEqual([row["sum"] for row in cost_rows], [12000.0, 12015.0])
