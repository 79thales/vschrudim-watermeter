"""Tests for retry merge and missing-hour detection."""
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest

PACKAGE = "vschrudim_watermeter"
root = Path(__file__).parents[1] / "custom_components" / PACKAGE
package = types.ModuleType(PACKAGE)
package.__path__ = [str(root)]
sys.modules[PACKAGE] = package
for name in ("models", "recovery"):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", root / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
models = sys.modules[f"{PACKAGE}.models"]
recovery = sys.modules[f"{PACKAGE}.recovery"]


class RecoveryTests(unittest.TestCase):
    def reading(self, hour: int, state: float, day: int = 1, month: int = 1):
        return models.MeterReading(datetime(2026, month, day, hour), state)

    def test_detects_individual_missing_hour(self):
        readings = (self.reading(0, 10), self.reading(2, 10.2))
        self.assertEqual(recovery.find_missing_hours(readings), (datetime(2026, 1, 1, 1),))

    def test_retry_merge_fills_gap_and_prefers_new_value(self):
        first = (self.reading(0, 10), self.reading(2, 10.2))
        retry = (self.reading(1, 10.1), self.reading(2, 10.25))
        merged = recovery.merge_readings(first, retry)
        self.assertEqual([row.meter_state_m3 for row in merged], [10, 10.1, 10.25])
        self.assertEqual(recovery.find_missing_hours(merged), ())

    def test_repeated_history_retry_has_one_reading_per_timestamp(self):
        original = (self.reading(0, 10), self.reading(1, 10.1))
        repeated = (self.reading(0, 10), self.reading(1, 10.15))
        merged = recovery.merge_readings(original, repeated, repeated)

        self.assertEqual(len(merged), 2)
        self.assertEqual([row.meter_state_m3 for row in merged], [10, 10.15])

    def test_czech_spring_dst_gap_is_not_reported(self):
        readings = (
            models.MeterReading(datetime(2026, 3, 29, 1), 10),
            models.MeterReading(datetime(2026, 3, 29, 3), 10.1),
        )
        self.assertEqual(recovery.find_missing_hours(readings), ())

    def test_empty_and_single_reading_have_no_internal_gap(self):
        self.assertEqual(recovery.find_missing_hours(()), ())
        self.assertEqual(recovery.find_missing_hours((self.reading(0, 10),)), ())
