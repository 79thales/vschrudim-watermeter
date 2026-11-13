"""Testy vazby přes registry a skutečná metadata, nikoli odhad podle názvu."""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "vschrudim_watermeter"
    / "dashboard_sources.py"
)
SPEC = importlib.util.spec_from_file_location("vschrudim_dashboard_sources", PATH)
SOURCES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCES)


def entity(**changes):
    values = {
        "platform": "vschrudim_watermeter",
        "config_entry_id": "entry_a",
        "device_id": "device_a",
        "disabled_by": None,
        "unique_id": "123_meter_state",
        "entity_id": "sensor.nove_jmeno",
    }
    values.update(changes)
    return SimpleNamespace(**values)


class DashboardSourcesTests(unittest.TestCase):
    def test_registry_device_and_entry_are_all_required(self):
        entries = [
            entity(),
            entity(config_entry_id="entry_b", entity_id="sensor.jiny_ucet"),
            entity(device_id="device_b", entity_id="sensor.jine_misto"),
            entity(disabled_by="user", unique_id="123_latest_consumption"),
            entity(platform="vschrudim", unique_id="123_water_price"),
        ]
        self.assertEqual(
            SOURCES.resolve_entities(
                entries, entry_id="entry_a", place_id="123", device_id="device_a"
            ),
            {"meter_state": "sensor.nove_jmeno"},
        )

    def test_metadata_must_exist_have_sum_and_correct_source_and_units(self):
        own = "vschrudim_watermeter:entry_water_consumption"
        cost = "vschrudim_watermeter:entry_water_cost"
        metadata = [
            {
                "source": "vschrudim_watermeter",
                "statistic_id": own,
                "has_sum": True,
                "statistics_unit_of_measurement": "m³",
            },
            {
                "source": "vschrudim_watermeter",
                "statistic_id": cost,
                "has_sum": True,
                "statistics_unit_of_measurement": "CZK",
            },
            {
                "source": "vschrudim",
                "statistic_id": "vschrudim:valid",
                "has_sum": True,
                "statistics_unit_of_measurement": "L",
            },
            {
                "source": "vschrudim",
                "statistic_id": "vschrudim:no_sum",
                "has_sum": False,
                "statistics_unit_of_measurement": "m³",
            },
            {
                "source": "vschrudim",
                "statistic_id": "vschrudim:bad_unit",
                "has_sum": True,
                "statistics_unit_of_measurement": "kWh",
            },
            {
                "source": "other",
                "statistic_id": "other:water",
                "has_sum": True,
                "statistics_unit_of_measurement": "m³",
            },
        ]
        resolved, candidates = SOURCES.resolve_statistics(
            metadata, own_consumption_id=own, own_cost_id=cost
        )
        self.assertEqual(resolved, {"consumption": own, "cost": cost})
        self.assertEqual(
            [item["statistic_id"] for item in candidates], ["vschrudim:valid"]
        )
        self.assertEqual(candidates[0]["unit"], "L")

    def test_litres_are_converted_and_invalid_values_are_not_invented(self):
        result = SOURCES.convert_to_m3(
            [{"sum": 120, "state": None}, {"sum": False}], "L"
        )
        self.assertEqual(result[0]["sum"], 0.12)
        self.assertIsNone(result[0]["state"])
        self.assertIs(result[1]["sum"], False)
        with self.assertRaises(ValueError):
            SOURCES.convert_to_m3([], "kWh")


if __name__ == "__main__":
    unittest.main()
