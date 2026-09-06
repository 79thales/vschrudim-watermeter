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
