"""Regrese přísného porovnání statistik bez závislosti na spuštěném HA."""

import importlib.util
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Načtení samotného výpočtu nevyvolá inicializaci integrace ani přístup k HA.
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "vschrudim_watermeter"
    / "statistics_compare.py"
)
SPEC = importlib.util.spec_from_file_location(
    "vschrudim_statistics_compare", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HOUR = timedelta(hours=1)


def points(first, values):
    """Bod Recorderu má začátek hodiny v UTC a kumulativní součet v m³."""
    return [
        {"start": first + index * HOUR, "sum": value, "state": value}
        for index, value in enumerate(values)
    ]


class StatisticsComparisonTests(unittest.TestCase):
    def test_different_sum_bases_compare_equal_hourly_usage(self):
        first = datetime(2026, 1, 1, tzinfo=UTC)
        result = MODULE.compare_hourly_statistics(
            points(first, [100, 100.125, 100.15]),
            points(first, [200, 200.125, 200.15]),
            first + HOUR,
            first + 3 * HOUR,
            as_of=first + 4 * HOUR,
        )
        self.assertEqual(result["comparable_hours"], 2)
        self.assertEqual(result["a_m3"], 0.15)
        self.assertEqual(result["difference_litres"], 0)
        self.assertFalse(result["days"][0]["complete"])
        self.assertFalse(result["months"][0]["complete"])
        self.assertEqual(result["months"][0]["comparable_hours"], 2)
        self.assertFalse(result["years"][0]["complete"])
        self.assertEqual(result["provenance"], "unknown")

    def test_missing_source_or_previous_hour_is_not_zero(self):
        first = datetime(2026, 1, 1, tzinfo=UTC)
        a = points(first, [10, 11, 12])
        result = MODULE.compare_hourly_statistics(
            a, [a[2]], first + HOUR, first + 3 * HOUR, as_of=first + 4 * HOUR
        )
        self.assertEqual(result["comparable_hours"], 0)
        self.assertIsNone(result["a_m3"])
        self.assertEqual(result["hours"][0]["status_b"], "missing")
        self.assertEqual(result["hours"][1]["status_b"], "missing_previous")
        self.assertIsNone(result["days"][0]["difference_m3"])

    def test_duplicate_reset_invalid_value_and_incomplete_hour(self):
        first = datetime(2026, 1, 1, tzinfo=UTC)
        a = points(first, [10, 11, 9, float("nan"), 12])
        b = points(first, [20, 21, 22, 23, 24])
        a.append(dict(a[1]))
        result = MODULE.compare_hourly_statistics(
            a, b, first + HOUR, first + 5 * HOUR, as_of=first + 4 * HOUR
        )
        self.assertEqual(result["anomalies_a"]["duplicate_timestamp"], 1)
        self.assertEqual(
            [row["status_a"] for row in result["hours"]],
            ["duplicate", "duplicate", "invalid_sum", "incomplete_hour"],
        )
        self.assertTrue(all(not row["comparable"] for row in result["hours"]))
        reset = MODULE.compare_hourly_statistics(
            points(first, [10, 9]),
            points(first, [20, 21]),
            first + HOUR,
            first + 2 * HOUR,
            as_of=first + 3 * HOUR,
        )
        self.assertEqual(reset["hours"][0]["status_a"], "sum_decreased")

    def test_unambiguous_boundary_and_timestamp_validation(self):
        first = datetime(2026, 1, 1, tzinfo=UTC)
        good = points(first, [10, 11])
        malformed = good + [
            {"start": "2026-01-01T00:00:00", "sum": 5},
            {"start": True, "sum": 5},
            {"start": first + timedelta(minutes=2), "sum": 5},
        ]
        result = MODULE.compare_hourly_statistics(
            malformed, good, first + HOUR, first + 2 * HOUR, as_of=first + 3 * HOUR
        )
        self.assertEqual(result["anomalies_a"]["invalid_timestamp"], 2)
        self.assertEqual(result["anomalies_a"]["unaligned_timestamp"], 1)
        with self.assertRaises(ValueError):
            MODULE.compare_hourly_statistics(
                good, good, first.replace(tzinfo=None), first + HOUR, as_of=first
            )
        with self.assertRaises(ValueError):
            MODULE.compare_hourly_statistics(
                good, good, first, first + timedelta(minutes=90), as_of=first
            )
        with self.assertRaises(ValueError):
            MODULE.compare_hourly_statistics(
                good, good, first, first + timedelta(days=1500), as_of=first
            )

    def test_prague_daylight_saving_days_and_repeated_local_hour(self):
        zone = ZoneInfo("Europe/Prague")
        for day, expected_hours in (
            (datetime(2026, 3, 29), 23),
            (datetime(2026, 10, 25), 25),
        ):
            with self.subTest(day=day):
                start = day.replace(tzinfo=zone).astimezone(UTC)
                end = (day + timedelta(days=1)).replace(tzinfo=zone).astimezone(UTC)
                values = list(range(expected_hours + 1))
                samples = points(start - HOUR, values)
                result = MODULE.compare_hourly_statistics(
                    samples, samples, start, end, as_of=end + HOUR
                )
                self.assertEqual(result["days"][0]["expected_hours"], expected_hours)
                self.assertTrue(result["days"][0]["complete"])
                self.assertEqual(result["comparable_hours"], expected_hours)
                if expected_hours == 25:
                    repeated = [
                        row["start_local"]
                        for row in result["hours"]
                        if "T02:00:00" in row["start_local"]
                    ]
                    self.assertEqual(len(repeated), 2)
                    self.assertNotEqual(repeated[0], repeated[1])

    def test_full_month_has_correct_dst_coverage(self):
        zone = ZoneInfo("Europe/Prague")
        start = datetime(2026, 3, 1, tzinfo=zone).astimezone(UTC)
        end = datetime(2026, 4, 1, tzinfo=zone).astimezone(UTC)
        expected_hours = int((end - start).total_seconds() // 3600)
        samples = points(start - HOUR, list(range(expected_hours + 1)))
        result = MODULE.compare_hourly_statistics(
            samples, samples, start, end, as_of=end + HOUR
        )
        self.assertEqual(expected_hours, 31 * 24 - 1)
        self.assertTrue(result["months"][0]["complete"])
        self.assertEqual(result["months"][0]["expected_hours"], expected_hours)
        self.assertFalse(result["years"][0]["complete"])


if __name__ == "__main__":
    unittest.main()
