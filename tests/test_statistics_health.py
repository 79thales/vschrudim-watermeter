"""Pure regression tests for privacy-safe Energy-statistics monitoring."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types
import unittest


PACKAGE = "vschrudim_watermeter_statistics_health_tests"
root = Path(__file__).parents[1] / "custom_components" / "vschrudim_watermeter"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(root)]
sys.modules[PACKAGE] = package
for name in ("models", "attempts", "statistics_health"):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{name}", root / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

models = sys.modules[f"{PACKAGE}.models"]
statistics_health = sys.modules[f"{PACKAGE}.statistics_health"]
MeterReading = models.MeterReading
StatisticsState = statistics_health.StatisticsState
assess_energy_statistics = statistics_health.assess_energy_statistics
assess_meter_register = statistics_health.assess_meter_register


UTC = timezone.utc


def _rows(*starts: datetime, sums: tuple[float, ...] | None = None):
    values = sums or tuple(float(index) for index in range(len(starts)))
    return [
        {"start": start, "state": value, "sum": value}
        for start, value in zip(starts, values)
    ]


class EnergyStatisticsHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = datetime(2026, 1, 1, 10, tzinfo=UTC)
        self.second = self.first + timedelta(hours=1)
        self.checked_at = self.second + timedelta(hours=1)

    def _assess(self, **overrides):
        options = {
            "expected_consumption_rows": _rows(self.first, self.second),
            "expected_cost_rows": _rows(self.first, self.second),
            "consumption_rows": _rows(self.first, self.second),
            "cost_rows": _rows(self.first, self.second),
            "portal_latest_timestamp": datetime(2025, 10, 13, 11),
            "write_pending": False,
            "checked_at": self.checked_at,
        }
        options.update(overrides)
        return assess_energy_statistics(**options)

    def test_matching_series_is_ok_even_when_portal_timestamp_is_old(self):
        health = self._assess()

        self.assertEqual(health.status, "ok")
        self.assertEqual(health.portal_latest_timestamp, datetime(2025, 10, 13, 11))
        self.assertEqual(health.missing_consumption_points, 0)
        self.assertTrue(health.consumption_sum_monotonic)

    def test_missing_cost_points_is_incomplete_without_blanking_consumption(self):
        health = self._assess(cost_rows=_rows(self.first))

        self.assertEqual(health.status, "incomplete")
        self.assertEqual(health.missing_consumption_points, 0)
        self.assertEqual(health.missing_cost_points, 1)

    def test_pending_writer_is_distinct_from_an_old_or_unchanged_source(self):
        health = self._assess(write_pending=True)

        self.assertEqual(health.status, "pending")
        self.assertTrue(health.write_pending)

    def test_duplicate_gap_and_non_monotonic_sum_are_all_reported(self):
        third = self.first + timedelta(hours=3)
        health = self._assess(
            consumption_rows=_rows(
                self.first,
                self.first,
                third,
                sums=(2.0, 1.0, 0.0),
            ),
        )

        self.assertEqual(health.status, "incomplete")
        self.assertEqual(health.consumption_duplicate_timestamps, 1)
        self.assertEqual(health.consumption_internal_gaps, 2)
        self.assertFalse(health.consumption_sum_monotonic)

    def test_utc_gaps_do_not_create_false_czech_dst_missing_hours(self):
        # Spring transition: local 02:00 does not exist, yet adjacent UTC
        # starts remain one hour apart. Autumn: both local 02:00 hours map to
        # distinct adjacent UTC starts. Neither may become a false gap.
        spring = datetime(2026, 3, 29, 0, tzinfo=UTC)
        autumn = datetime(2026, 10, 25, 0, tzinfo=UTC)
        for start in (spring, autumn):
            with self.subTest(start=start):
                health = assess_energy_statistics(
                    expected_consumption_rows=_rows(start, start + timedelta(hours=1)),
                    expected_cost_rows=_rows(start, start + timedelta(hours=1)),
                    consumption_rows=_rows(start, start + timedelta(hours=1)),
                    cost_rows=_rows(start, start + timedelta(hours=1)),
                    portal_latest_timestamp=start,
                    write_pending=False,
                    checked_at=self.checked_at,
                )
                self.assertEqual(health.status, "ok")
                self.assertEqual(health.consumption_internal_gaps, 0)
                self.assertEqual(health.cost_internal_gaps, 0)

    def test_unknown_is_used_when_no_completed_portal_points_exist(self):
        health = assess_energy_statistics(
            expected_consumption_rows=[],
            expected_cost_rows=[],
            consumption_rows=[],
            cost_rows=[],
            portal_latest_timestamp=datetime(2026, 9, 8, 10),
            write_pending=False,
            checked_at=self.checked_at,
        )

        self.assertEqual(health.status, "unknown")


class StatisticsStateTests(unittest.TestCase):
    def test_store_state_drops_malformed_and_sensitive_values(self):
        state = StatisticsState.from_dict(
            {
                "status": "pending",
                "last_attempt_at": "2026-01-01T10:00:00+01:00",
                "last_error_type": "RuntimeError",
                "last_error": "token=top-secret https://example.invalid/private",
                "last_written_points": "3",
                "recovery_pending": True,
                "health_status": "incomplete",
                "last_health_check_at": "not-a-date",
            }
        )
        serialized = state.as_dict()

        self.assertEqual(state.status, "pending")
        self.assertEqual(state.last_written_points, 3)
        self.assertIsNone(state.last_health_check_at)
        self.assertNotIn("top-secret", serialized["last_error"] or "")
        self.assertNotIn("example.invalid", serialized["last_error"] or "")

    def test_store_state_defaults_for_malformed_input(self):
        self.assertEqual(StatisticsState.from_dict({"status": "broken"}).status, "never")
        self.assertEqual(StatisticsState.from_dict(None).health_status, "unknown")


class MeterRegisterHealthTests(unittest.TestCase):
    def test_lower_register_is_informational_and_does_not_create_a_spike(self):
        readings = (
            MeterReading(datetime(2026, 1, 1, 10), 100.0),
            MeterReading(datetime(2026, 1, 1, 11), 100.5),
            MeterReading(datetime(2026, 1, 1, 12), 29.69),
            MeterReading(datetime(2026, 1, 1, 13), 29.89),
        )
        health = assess_meter_register(
            readings, checked_at=datetime(2026, 1, 1, 14, tzinfo=UTC)
        )

        self.assertEqual(health.status, "possible_reset_or_correction")
        self.assertEqual(health.new_decrease_timestamp, datetime(2026, 1, 1, 12))
        self.assertEqual(health.decrease_count_current_readings, 1)
