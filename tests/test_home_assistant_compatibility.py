"""Smoke tests run in CI with supported Home Assistant releases."""
from __future__ import annotations

import importlib
import importlib.util
import asyncio
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

HOME_ASSISTANT_INSTALLED = importlib.util.find_spec("homeassistant") is not None


@unittest.skipUnless(HOME_ASSISTANT_INSTALLED, "Home Assistant is installed only in compatibility CI")
class HomeAssistantCompatibilityTests(unittest.TestCase):
    def test_all_integration_modules_import(self):
        for module in (
            "custom_components.vschrudim_watermeter",
            "custom_components.vschrudim_watermeter.api",
            "custom_components.vschrudim_watermeter.attempts",
            "custom_components.vschrudim_watermeter.calculation",
            "custom_components.vschrudim_watermeter.config_flow",
            "custom_components.vschrudim_watermeter.coordinator",
            "custom_components.vschrudim_watermeter.diagnostics",
            "custom_components.vschrudim_watermeter.history",
            "custom_components.vschrudim_watermeter.models",
            "custom_components.vschrudim_watermeter.recovery",
            "custom_components.vschrudim_watermeter.services",
            "custom_components.vschrudim_watermeter.sensor",
        ):
            with self.subTest(module=module):
                importlib.import_module(module)

    def test_coordinator_price_dependencies(self):
        from custom_components.vschrudim_watermeter import coordinator

        self.assertEqual(coordinator.CONF_PRICE_PER_M3, "price_per_m3")
        self.assertEqual(coordinator.DEFAULT_PRICE_PER_M3, 0.0)

    def test_water_sensor_contract(self):
        from homeassistant.components.sensor import SensorDeviceClass
        from homeassistant.const import UnitOfVolume
        from custom_components.vschrudim_watermeter.sensor import WaterMeterStateSensor

        sensor = object.__new__(WaterMeterStateSensor)
        self.assertEqual(sensor.device_class, SensorDeviceClass.WATER)
        self.assertIsNone(sensor.state_class)
        self.assertEqual(sensor.native_unit_of_measurement, UnitOfVolume.CUBIC_METERS)

        from custom_components.vschrudim_watermeter.sensor import TotalWaterCostSensor

        cost_sensor = object.__new__(TotalWaterCostSensor)
        self.assertEqual(cost_sensor.device_class, SensorDeviceClass.MONETARY)
        self.assertIsNone(cost_sensor.state_class)
        self.assertEqual(cost_sensor.native_unit_of_measurement, "CZK")

    def test_external_history_statistics_are_monotonic(self):
        from custom_components.vschrudim_watermeter.history import (
            async_add_external_meter_statistics,
            meter_statistics,
        )
        from custom_components.vschrudim_watermeter.models import MeterReading

        rows = meter_statistics(
            (
                MeterReading(datetime(2026, 1, 1, 10), 100.0),
                MeterReading(datetime(2026, 1, 1, 11), 100.125),
            ),
            local_tz=ZoneInfo("Europe/Prague"),
            now=datetime(2026, 1, 1, 13, tzinfo=ZoneInfo("Europe/Prague")),
        )
        self.assertEqual([row["sum"] for row in rows], [0.0, 0.125])
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
        self.assertEqual([row["sum"] for row in cost_rows], [0.0, 15.0])

        with patch(
            "custom_components.vschrudim_watermeter.history.async_add_external_statistics"
        ) as add_external:
            async_add_external_meter_statistics(
                object(),
                statistic_id="vschrudim_watermeter:test_water_consumption",
                readings=(MeterReading(datetime(2026, 1, 1, 10), 100.0),),
                local_tz=ZoneInfo("Europe/Prague"),
                now=datetime(2026, 1, 1, 13, tzinfo=ZoneInfo("Europe/Prague")),
            )
        metadata = add_external.call_args.args[1]
        self.assertEqual(metadata["statistic_id"], "vschrudim_watermeter:test_water_consumption")
        self.assertEqual(metadata["source"], "vschrudim_watermeter")

    def test_download_attempt_records_success_and_failure(self):
        from custom_components.vschrudim_watermeter.attempts import DownloadAttempt
        from custom_components.vschrudim_watermeter.coordinator import VsChrudimCoordinator
        from custom_components.vschrudim_watermeter.models import DownloadMetadata, MeterReading

        class Store:
            async def async_save(self, value):
                self.value = value

        async def run_test():
            coordinator = object.__new__(VsChrudimCoordinator)
            coordinator._attempt_store = Store()
            coordinator.download_attempt_history = []
            started = datetime(2026, 1, 1, 10, tzinfo=ZoneInfo("Europe/Prague"))
            await coordinator._async_record_download_attempt(
                started,
                0.0,
                result="success",
                metadata=DownloadMetadata(source="html_table", html_table_detected=True),
                readings=(MeterReading(datetime(2026, 1, 1, 9), 10.0),),
                missing_hourly_readings=0,
            )
            await coordinator._async_record_download_attempt(
                started,
                0.0,
                result="failed",
                error=ValueError("token=secret https://example.invalid/private"),
            )
            return coordinator

        coordinator = asyncio.run(run_test())
        self.assertEqual(len(coordinator.download_attempt_history), 2)
        self.assertEqual(coordinator.download_attempt_history[0].source, "html_table")
        self.assertEqual(coordinator.download_attempt_history[1].result, "failed")
        self.assertNotIn("secret", coordinator.download_attempt_history[1].error or "")

    def test_diagnostics_redacts_attempt_history_and_returns_newest_first(self):
        from custom_components.vschrudim_watermeter.attempts import DownloadAttempt
        from custom_components.vschrudim_watermeter.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        older = DownloadAttempt(
            started_at="2026-01-01T10:00:00+01:00",
            finished_at="2026-01-01T10:00:01+01:00",
            duration_ms=100,
            result="success",
        )
        newer = DownloadAttempt(
            started_at="2026-01-01T11:00:00+01:00",
            finished_at="2026-01-01T11:00:01+01:00",
            duration_ms=100,
            result="failed",
            error="password=secret",
        )
        coordinator = SimpleNamespace(
            data=None,
            last_attempt_at=None,
            last_success_at=None,
            last_attempt_result="failed",
            last_attempt_error="password=secret",
            download_attempt_history=[older, newer],
            consumption_statistic_id="vschrudim_watermeter:example_water_consumption",
            cost_statistic_id="vschrudim_watermeter:example_water_cost",
            statistics_ready=True,
            history_backfill_status="completed",
            history_backfill_scan_start=None,
            history_backfill_cursor=None,
            history_earliest_date=None,
            history_backfill_processed_chunks=1,
            history_backfill_total_chunks=1,
            history_backfill_imported_hours=1,
            history_backfill_error=None,
        )
        entry = SimpleNamespace(
            runtime_data=coordinator,
            data={"username": "user", "password": "secret", "place": "private"},
            options={},
        )
        diagnostics = asyncio.run(async_get_config_entry_diagnostics(None, entry))
        history = diagnostics["download_attempt_history"]
        self.assertEqual(history[0]["result"], "failed")
        self.assertNotIn("secret", str(diagnostics))
        self.assertNotIn("private", str(diagnostics))
