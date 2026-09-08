"""Smoke tests run in CI with supported Home Assistant releases."""
from __future__ import annotations

import importlib
import importlib.util
import asyncio
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

HOME_ASSISTANT_INSTALLED = importlib.util.find_spec("homeassistant") is not None


@unittest.skipUnless(HOME_ASSISTANT_INSTALLED, "Home Assistant is installed only in compatibility CI")
class HomeAssistantCompatibilityTests(unittest.TestCase):
    def test_all_integration_modules_import(self):
        for module in (
            "custom_components.vschrudim_watermeter",
            "custom_components.vschrudim_watermeter.api",
            "custom_components.vschrudim_watermeter.attempts",
            "custom_components.vschrudim_watermeter.button",
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

        from custom_components.vschrudim_watermeter.sensor import (
            LatestPortalReadingSensor,
        )

        latest_sensor = SimpleNamespace(
            coordinator=SimpleNamespace(
                data=SimpleNamespace(
                    readings=(
                        SimpleNamespace(timestamp=datetime(2026, 9, 6, 15, 0)),
                    )
                )
            )
        )
        self.assertEqual(
            LatestPortalReadingSensor.native_value.fget(latest_sensor),
            "06.09.2026 15:00",
        )

    def test_source_delay_status_requires_an_explicit_user_threshold(self):
        from custom_components.vschrudim_watermeter import coordinator as module
        from custom_components.vschrudim_watermeter.coordinator import (
            VsChrudimCoordinator,
        )
        from custom_components.vschrudim_watermeter.models import MeterReading

        coordinator = object.__new__(VsChrudimCoordinator)
        coordinator.hass = SimpleNamespace(
            config=SimpleNamespace(time_zone="Europe/Prague")
        )
        coordinator.entry = SimpleNamespace(options={})
        reading = MeterReading(datetime(2026, 9, 6, 15, 0), 10.0)

        with patch.object(
            module.dt_util,
            "now",
            return_value=datetime(2026, 9, 8, 15, tzinfo=ZoneInfo("Europe/Prague")),
        ):
            self.assertEqual(
                coordinator._source_status_for_readings((reading,)), "ok"
            )
            coordinator.entry.options = {"source_delay_warning_hours": 1}
            self.assertEqual(
                coordinator._source_status_for_readings((reading,)),
                "delayed_data",
            )

    def test_failed_polling_uses_bounded_exponential_backoff(self):
        from custom_components.vschrudim_watermeter.coordinator import (
            _MAX_FAILURE_RETRY_SECONDS,
            _failure_retry_after,
        )

        self.assertEqual(_failure_retry_after(1, 30), 60)
        self.assertEqual(_failure_retry_after(2, 30), 120)
        self.assertEqual(_failure_retry_after(4, 30), 480)
        self.assertEqual(
            _failure_retry_after(999, 900), _MAX_FAILURE_RETRY_SECONDS
        )

    def test_source_notifications_only_follow_problem_state_changes(self):
        from custom_components.vschrudim_watermeter import coordinator as module
        from custom_components.vschrudim_watermeter.coordinator import (
            VsChrudimCoordinator,
        )

        coordinator = object.__new__(VsChrudimCoordinator)
        coordinator.hass = SimpleNamespace()
        coordinator.entry = SimpleNamespace(
            entry_id="test", options={"notify_unavailable": True}
        )
        coordinator.source_status = "unknown"
        coordinator._source_problem_notification_active = False
        coordinator._async_save_notification_state = AsyncMock()

        with (
            patch.object(module, "async_create") as create,
            patch.object(module, "async_dismiss"),
        ):
            async def change_states():
                await coordinator._set_source_status(
                    "error",
                    error=ValueError("portal unavailable"),
                    notify_problem=True,
                )
                await coordinator._set_source_status(
                    "error",
                    error=ValueError("portal unavailable"),
                    notify_problem=True,
                )
                await coordinator._set_source_status("delayed_data")
                await coordinator._set_source_status("ok")

            asyncio.run(change_states())

        self.assertEqual(create.call_count, 2)
        self.assertEqual(coordinator.source_status, "ok")

    def test_missing_reading_notifications_only_follow_state_changes(self):
        from custom_components.vschrudim_watermeter import coordinator as module
        from custom_components.vschrudim_watermeter.coordinator import (
            VsChrudimCoordinator,
        )

        coordinator = object.__new__(VsChrudimCoordinator)
        coordinator.hass = SimpleNamespace()
        coordinator.entry = SimpleNamespace(
            entry_id="test", options={"notify_missing": True}
        )
        coordinator._missing_problem_notification_active = False
        coordinator._async_save_notification_state = AsyncMock()
        missing = (datetime(2026, 1, 1, 11),)

        with (
            patch.object(module, "async_create") as create,
            patch.object(module, "async_dismiss"),
        ):
            async def change_states():
                await coordinator._handle_missing_hours_notification(missing, 2)
                await coordinator._handle_missing_hours_notification(missing, 2)
                await coordinator._handle_missing_hours_notification((), 0)

            asyncio.run(change_states())

        self.assertEqual(create.call_count, 2)
        self.assertFalse(coordinator._missing_problem_notification_active)

    def test_notification_transition_state_survives_a_restart(self):
        from custom_components.vschrudim_watermeter.coordinator import (
            VsChrudimCoordinator,
        )

        class Store:
            async def async_load(self):
                return {
                    "source_status": "authentication_required",
                    "source_problem_notification_active": True,
                    "missing_problem_notification_active": True,
                }

        coordinator = object.__new__(VsChrudimCoordinator)
        coordinator._notification_store = Store()
        coordinator.source_status = "unknown"
        coordinator._source_problem_notification_active = False
        coordinator._missing_problem_notification_active = False

        asyncio.run(coordinator._async_load_notification_state())

        self.assertEqual(coordinator.source_status, "authentication_required")
        self.assertTrue(coordinator._source_problem_notification_active)
        self.assertTrue(coordinator._missing_problem_notification_active)

    def test_shutdown_cancels_a_running_history_task(self):
        from custom_components.vschrudim_watermeter.coordinator import (
            VsChrudimCoordinator,
        )

        async def run_test():
            coordinator = object.__new__(VsChrudimCoordinator)
            waiting = asyncio.Event()
            coordinator._history_backfill_task = asyncio.create_task(waiting.wait())
            await asyncio.sleep(0)
            await coordinator.async_shutdown()
            return coordinator

        coordinator = asyncio.run(run_test())
        self.assertIsNone(coordinator._history_backfill_task)

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

    def test_meter_reset_starts_a_new_baseline_without_a_consumption_spike(self):
        from custom_components.vschrudim_watermeter.history import meter_statistics
        from custom_components.vschrudim_watermeter.models import MeterReading

        rows = meter_statistics(
            (
                MeterReading(datetime(2026, 1, 1, 10), 100.0),
                MeterReading(datetime(2026, 1, 1, 11), 100.5),
                MeterReading(datetime(2026, 1, 1, 12), 29.69),
                MeterReading(datetime(2026, 1, 1, 13), 29.89),
            ),
            local_tz=ZoneInfo("Europe/Prague"),
            now=datetime(2026, 1, 1, 14, tzinfo=ZoneInfo("Europe/Prague")),
        )

        self.assertEqual([row["sum"] for row in rows], [0.0, 0.5, 0.5, 0.7])

    def test_rebuild_verification_builds_both_supported_statistic_series(self):
        from custom_components.vschrudim_watermeter import coordinator as module
        from custom_components.vschrudim_watermeter.coordinator import (
            VsChrudimCoordinator,
        )
        from custom_components.vschrudim_watermeter.history import (
            cost_statistics,
            meter_statistics,
        )
        from custom_components.vschrudim_watermeter.models import MeterReading

        now = datetime(2026, 1, 1, 14, tzinfo=ZoneInfo("Europe/Prague"))
        readings = (
            MeterReading(datetime(2026, 1, 1, 10), 100.0),
            MeterReading(datetime(2026, 1, 1, 11), 100.5),
        )
        local_tz = ZoneInfo("Europe/Prague")
        coordinator = object.__new__(VsChrudimCoordinator)
        coordinator.hass = SimpleNamespace(
            config=SimpleNamespace(time_zone="Europe/Prague")
        )
        coordinator.entry = SimpleNamespace(
            entry_id="TEST", options={"price_per_m3": 120.0}
        )
        expected = {
            coordinator.consumption_statistic_id: meter_statistics(
                readings, local_tz=local_tz, now=now
            ),
            coordinator.cost_statistic_id: cost_statistics(
                readings, price_per_m3=120.0, local_tz=local_tz, now=now
            ),
        }

        class Recorder:
            async def async_add_executor_job(self, target, *args):
                return target(*args)

        def last_statistics(*args):
            statistic_id = args[2]
            return {
                statistic_id: [
                    {"start": expected[statistic_id][-1]["start"].timestamp()}
                ]
            }

        def period_statistics(*args):
            statistic_id = next(iter(args[3]))
            return {
                statistic_id: [
                    {"sum": row["sum"]} for row in expected[statistic_id]
                ]
            }

        with (
            patch.object(module.dt_util, "now", return_value=now),
            patch.object(module, "get_instance", return_value=Recorder()),
            patch.object(module, "get_last_statistics", side_effect=last_statistics),
            patch.object(
                module,
                "statistics_during_period",
                side_effect=period_statistics,
            ),
        ):
            asyncio.run(coordinator._async_verify_energy_statistics(readings))

    def test_rebuild_button_is_explicit_config_action(self):
        from homeassistant.const import EntityCategory
        from custom_components.vschrudim_watermeter.button import (
            RebuildEnergyStatisticsButton,
        )

        coordinator = SimpleNamespace(
            place=SimpleNamespace(identifier="test", address="Test meter"),
            statistics_operation_running=False,
            async_rebuild_energy_statistics=AsyncMock(),
        )
        button = RebuildEnergyStatisticsButton(coordinator)

        self.assertEqual(button.entity_category, EntityCategory.CONFIG)
        self.assertTrue(button.available)
        asyncio.run(button.async_press())
        coordinator.async_rebuild_energy_statistics.assert_awaited_once_with(
            confirm=True
        )

    def test_retry_history_button_does_not_rebuild_statistics(self):
        from homeassistant.const import EntityCategory
        from custom_components.vschrudim_watermeter.button import (
            RetryHistoryDownloadButton,
        )

        coordinator = SimpleNamespace(
            place=SimpleNamespace(identifier="test", address="Test meter"),
            history_backfill_running=False,
            statistics_operation_running=False,
            async_retry_history_download=AsyncMock(),
        )
        button = RetryHistoryDownloadButton(coordinator)

        self.assertEqual(button.entity_category, EntityCategory.DIAGNOSTIC)
        self.assertTrue(button.available)
        asyncio.run(button.async_press())
        coordinator.async_retry_history_download.assert_awaited_once_with()

    def test_download_test_button_does_not_start_history_or_statistics_work(self):
        from homeassistant.const import EntityCategory
        from custom_components.vschrudim_watermeter.button import TestDownloadButton

        coordinator = SimpleNamespace(
            place=SimpleNamespace(identifier="test", address="Test meter"),
            statistics_operation_running=False,
            async_test_download=AsyncMock(),
        )
        button = TestDownloadButton(coordinator)

        self.assertEqual(button.entity_category, EntityCategory.DIAGNOSTIC)
        self.assertTrue(button.available)
        asyncio.run(button.async_press())
        coordinator.async_test_download.assert_awaited_once_with()

    def test_download_test_fetches_without_importing_or_starting_backfill(self):
        from custom_components.vschrudim_watermeter.coordinator import (
            VsChrudimCoordinator,
        )
        from custom_components.vschrudim_watermeter.models import (
            ConsumptionPlace,
            DownloadMetadata,
            MeterReading,
            WaterMeterData,
        )

        place = ConsumptionPlace("test", "", "", "", "")
        data = WaterMeterData(
            place,
            (MeterReading(datetime(2026, 1, 1, 10), 10.0),),
            0.0,
            download_metadata=DownloadMetadata(source="html_table"),
        )
        coordinator = object.__new__(VsChrudimCoordinator)
        coordinator._statistics_operation_lock = asyncio.Lock()
        coordinator._api_lock = asyncio.Lock()
        coordinator.client = SimpleNamespace(async_get_data=AsyncMock(return_value=data))
        coordinator.place = place
        coordinator.entry = SimpleNamespace(options={})
        coordinator.last_test_download_at = None
        coordinator.last_test_download_result = "never"
        coordinator.last_test_download_error = None
        coordinator.last_download_source = "unknown"
        coordinator.last_download_latest_timestamp = None
        coordinator.last_download_reading_count = 0
        coordinator.last_duplicate_readings_merged = 0
        coordinator.last_missing_readings_recovered = 0
        coordinator.current_missing_hourly_readings = 0
        coordinator.oldest_missing_hour = None
        coordinator.last_success_at = None
        coordinator._set_source_status = AsyncMock()
        coordinator._handle_missing_hours_notification = AsyncMock()
        coordinator.async_update_listeners = Mock()
        coordinator._async_import_readings = AsyncMock()
        coordinator.async_start_history_backfill = Mock()

        asyncio.run(coordinator.async_test_download())

        coordinator._async_import_readings.assert_not_awaited()
        coordinator.async_start_history_backfill.assert_not_called()
        coordinator._handle_missing_hours_notification.assert_not_awaited()
        self.assertEqual(coordinator.last_test_download_result, "success")
        self.assertEqual(coordinator.last_download_source, "html_table")
        self.assertEqual(
            coordinator.last_download_latest_timestamp,
            datetime(2026, 1, 1, 10),
        )
        self.assertEqual(coordinator.last_download_reading_count, 1)

    def test_current_gap_detection_ignores_old_backfill_gaps(self):
        from custom_components.vschrudim_watermeter.coordinator import (
            VsChrudimCoordinator,
        )
        from custom_components.vschrudim_watermeter.models import (
            ConsumptionPlace,
            MeterReading,
            WaterMeterData,
        )

        place = ConsumptionPlace("test", "", "", "", "")
        current_data = WaterMeterData(
            place,
            (
                MeterReading(datetime(2026, 9, 7, 10), 100.0),
                MeterReading(datetime(2026, 9, 7, 11), 100.1),
            ),
            0.1,
        )
        coordinator = object.__new__(VsChrudimCoordinator)
        coordinator._api_lock = asyncio.Lock()
        coordinator.client = SimpleNamespace(
            async_get_data=AsyncMock(return_value=current_data)
        )
        coordinator.place = place
        coordinator.entry = SimpleNamespace(
            options={"missing_retry_attempts": 0, "retry_delay": 5}
        )
        coordinator.hass = SimpleNamespace(loop=SimpleNamespace(call_soon=Mock()))
        coordinator._known_readings = (
            MeterReading(datetime(2025, 10, 13, 10), 90.0),
            MeterReading(datetime(2025, 10, 26, 5), 91.0),
        )
        coordinator._consecutive_failures = 0
        coordinator._set_source_status = AsyncMock()
        coordinator._handle_missing_hours_notification = AsyncMock()
        coordinator._async_import_readings = AsyncMock()
        coordinator._async_record_download_attempt = AsyncMock()
        coordinator.history_backfill_status = "not_started"

        result = asyncio.run(coordinator._async_update_data())

        self.assertEqual(result.missing_timestamps, ())
        self.assertEqual(coordinator.current_missing_hourly_readings, 0)
        coordinator.client.async_get_data.assert_awaited_once_with(place)
        coordinator._handle_missing_hours_notification.assert_awaited_once_with((), 0)

    def test_failed_download_keeps_known_readings_unchanged(self):
        from homeassistant.helpers.update_coordinator import UpdateFailed
        from custom_components.vschrudim_watermeter.api import VsChrudimProtocolError
        from custom_components.vschrudim_watermeter.coordinator import (
            VsChrudimCoordinator,
        )
        from custom_components.vschrudim_watermeter.models import MeterReading

        known = (MeterReading(datetime(2026, 9, 7, 10), 100.0),)
        coordinator = object.__new__(VsChrudimCoordinator)
        coordinator._api_lock = asyncio.Lock()
        coordinator.client = SimpleNamespace(
            async_get_data=AsyncMock(side_effect=VsChrudimProtocolError("changed"))
        )
        coordinator.place = SimpleNamespace()
        coordinator.entry = SimpleNamespace(
            options={"failure_threshold": 3, "retry_delay": 5}
        )
        coordinator._known_readings = known
        coordinator._consecutive_failures = 0
        coordinator._set_source_status = AsyncMock()
        coordinator._async_record_download_attempt = AsyncMock()

        with self.assertRaises(UpdateFailed):
            asyncio.run(coordinator._async_update_data())

        self.assertEqual(coordinator._known_readings, known)
        coordinator._async_record_download_attempt.assert_awaited_once()

    def test_unexpected_update_error_is_isolated_and_uses_safe_retry(self):
        from homeassistant.helpers.update_coordinator import UpdateFailed
        from custom_components.vschrudim_watermeter.coordinator import (
            VsChrudimCoordinator,
        )
        from custom_components.vschrudim_watermeter.models import MeterReading

        known = (MeterReading(datetime(2026, 9, 7, 10), 100.0),)
        coordinator = object.__new__(VsChrudimCoordinator)
        coordinator._api_lock = asyncio.Lock()
        coordinator.client = SimpleNamespace(
            async_get_data=AsyncMock(side_effect=RuntimeError("private payload"))
        )
        coordinator.place = SimpleNamespace()
        coordinator.entry = SimpleNamespace(
            options={"failure_threshold": 3, "retry_delay": 30}
        )
        coordinator._known_readings = known
        coordinator._consecutive_failures = 0
        coordinator._set_source_status = AsyncMock()
        coordinator._async_record_download_attempt = AsyncMock()

        with self.assertRaisesRegex(UpdateFailed, "Unexpected VSChrudim"):
            asyncio.run(coordinator._async_update_data())

        self.assertEqual(coordinator._known_readings, known)
        self.assertEqual(
            coordinator.last_attempt_error, "Unexpected internal update error"
        )
        coordinator._async_record_download_attempt.assert_awaited_once()

    def test_retry_history_download_refreshes_before_starting_backfill(self):
        from custom_components.vschrudim_watermeter.coordinator import (
            VsChrudimCoordinator,
        )

        class Lock:
            @staticmethod
            def locked():
                return False

        async def refresh():
            coordinator.last_attempt_result = "success"

        coordinator = object.__new__(VsChrudimCoordinator)
        coordinator._statistics_operation_lock = Lock()
        coordinator._history_backfill_task = None
        coordinator.last_attempt_result = "never"
        coordinator.last_attempt_error = None
        coordinator.async_request_refresh = refresh
        coordinator.async_start_history_backfill = Mock(return_value=True)

        asyncio.run(coordinator.async_retry_history_download())

        coordinator.async_start_history_backfill.assert_called_once_with()

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
                metadata=DownloadMetadata(
                    source="html_table",
                    html_table_detected=True,
                    portal_page_features=("html_table", "webforms_form"),
                    reading_quality_flags=("meter_state_decreased",),
                ),
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
        self.assertEqual(
            coordinator.download_attempt_history[0].portal_page_features,
            ("html_table", "webforms_form"),
        )
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
            error="password=credential-token-73921",
        )
        coordinator = SimpleNamespace(
            data=None,
            last_attempt_at=None,
            last_success_at=None,
            last_attempt_result="failed",
            last_attempt_error="password=credential-token-73921",
            source_status="error",
            last_download_source="unknown",
            last_download_latest_timestamp=None,
            last_download_reading_count=0,
            last_portal_page_features=("html_table",),
            last_reading_quality_flags=("meter_state_decreased",),
            last_duplicate_readings_merged=0,
            last_missing_readings_recovered=0,
            current_missing_hourly_readings=0,
            oldest_missing_hour=None,
            last_test_download_at=None,
            last_test_download_result="never",
            last_test_download_error=None,
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
            data={
                "username": "user",
                "password": "credential-token-73921",
                "place": "private",
            },
            options={},
        )
        diagnostics = asyncio.run(async_get_config_entry_diagnostics(None, entry))
        history = diagnostics["download_attempt_history"]
        self.assertEqual(history[0]["result"], "failed")
        self.assertEqual(diagnostics["entry"]["data"]["password"], "**REDACTED**")
        self.assertEqual(diagnostics["entry"]["data"]["place"], "**REDACTED**")
        self.assertNotEqual(
            diagnostics["last_attempt_error"], "password=credential-token-73921"
        )
        self.assertNotEqual(history[0]["error"], "password=credential-token-73921")
        self.assertEqual(diagnostics["last_portal_page_features"], ["html_table"])
        self.assertEqual(
            diagnostics["last_reading_quality_flags"], ["meter_state_decreased"]
        )
