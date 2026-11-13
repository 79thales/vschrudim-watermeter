"""Testy šablony: zachování správných vazeb a bezpečný výstup bez HA."""

import importlib.util
import unittest
from pathlib import Path

PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "vschrudim_watermeter"
    / "dashboard.py"
)
SPEC = importlib.util.spec_from_file_location("vschrudim_dashboard_test", PATH)
DASHBOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DASHBOARD)


def build(**changes):
    """Výslovné registry hodnoty simulují uživatelem přejmenované entity."""
    arguments = {
        "title": "Voda",
        "url_path": "voda-chrudim",
        "language": "cs",
        "entities": {
            "meter_state": "sensor.prejmenovany_vodomer",
            "last_update_attempt": "sensor.prejmenovany_pokus",
            "test_download": "button.test",
        },
        "statistics": {
            "consumption": "vschrudim_watermeter:entry_water_consumption",
            "cost": "vschrudim_watermeter:entry_water_cost",
        },
    }
    arguments.update(changes)
    return DASHBOARD.build_dashboard(**arguments)


def all_cards(config):
    return [
        card
        for view in config["views"]
        for section in view["sections"]
        for card in section["cards"]
    ]


class DashboardGeneratorTests(unittest.TestCase):
    def test_template_keeps_main_and_detail_and_renamed_entities(self):
        config = build()
        self.assertEqual(
            [v["path"] for v in config["views"]],
            ["voda-chrudim", "voda-chrudim-detail"],
        )
        self.assertTrue(config["views"][0]["show_icon_and_title"])
        self.assertTrue(config["views"][1]["subview"])
        self.assertIn("sensor.prejmenovany_vodomer", repr(config))
        self.assertIn("sensor.prejmenovany_pokus", repr(config))
        self.assertIn("last_success", repr(config))
        self.assertNotIn("sensor.meter_state", repr(config))
        self.assertEqual(
            len([card for card in all_cards(config) if card["type"] == "statistic"]), 6
        )

    def test_missing_entities_and_statistics_do_not_create_guesses(self):
        config = build(entities={}, statistics={})
        self.assertNotIn("sensor.", repr(config))
        self.assertNotIn("vschrudim_watermeter:", repr(config))
        self.assertFalse(any(card["type"] == "statistic" for card in all_cards(config)))

    def test_comparison_uses_same_axis_and_explicit_source(self):
        source = "vschrudim:explicitly_selected_watermeter"
        cards = all_cards(build(comparison=source, comparison_unit="m³"))
        graphs = [card for card in cards if card["type"] == "statistics-graph"]
        self.assertEqual([card["period"] for card in graphs], ["hour", "day", "month"])
        self.assertTrue(
            all(
                card["entities"]
                == ["vschrudim_watermeter:entry_water_consumption", source]
                for card in graphs
            )
        )
        self.assertEqual(
            len(
                [
                    card
                    for card in cards
                    if card["type"] == "custom:vschrudim-statistics-comparison"
                ]
            ),
            1,
        )

    def test_template_not_mutated_between_previews(self):
        first = build(comparison="vschrudim:other", comparison_unit="m³")
        second = build(entities={}, statistics={})
        self.assertIn("vschrudim:other", repr(first))
        self.assertNotIn("vschrudim:other", repr(second))

    def test_litre_source_is_only_in_strictly_converted_comparison(self):
        config = build(comparison="vschrudim:litres", comparison_unit="L")
        graphs = [
            card for card in all_cards(config) if card["type"] == "statistics-graph"
        ]
        self.assertTrue(
            all("vschrudim:litres" not in card["entities"] for card in graphs)
        )
        self.assertIn("custom:vschrudim-statistics-comparison", repr(config))

    def test_navigation_points_to_detail_in_overview(self):
        self.assertIn("/lovelace/voda-chrudim-detail", repr(build()))
        with self.assertRaises(ValueError):
            build(target="new")

    def test_invalid_paths_entities_and_language_are_rejected(self):
        for changes in (
            {"url_path": "../lovelace"},
            {"url_path": "overview"},
            {"title": "x\n"},
            {"entities": {"meter_state": "sensor.a\nhacked"}},
            {"comparison": "https://invalid"},
            {"language": "de"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build(**changes)


if __name__ == "__main__":
    unittest.main()
