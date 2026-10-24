"""Regrese výběru přesných závislostí CI; testy nevyužívají síť ani běžící HA."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import unittest
from unittest.mock import patch
from urllib.error import URLError

from scripts.latest_ha_requirement import latest_requirement, main, matching_plugin_requirement


class LatestHomeAssistantRequirementTest(unittest.TestCase):
    def test_exact_stable_version_is_used(self) -> None:
        for version in ("2026.9.4", "2027.1.0"):
            with self.subTest(version=version):
                self.assertEqual(latest_requirement({"version": version}), f"homeassistant=={version}")

    def test_beta_and_invalid_metadata_are_rejected(self) -> None:
        for version in ("2026.10.0b2", "2026.10.0rc1", "latest", None, "", "2026.9.4;invalid"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                latest_requirement({"version": version})


class MatchingPluginRequirementTest(unittest.TestCase):
    """Odmítnout skutečný fallback na 0.2.1 i zdánlivě kompatibilní rozsahy."""

    def test_exact_matching_plugin_is_pinned(self) -> None:
        for dependency in ("homeassistant==2026.10.2", "homeassistant (==2026.10.2)"):
            with self.subTest(dependency=dependency):
                self.assertEqual(
                    matching_plugin_requirement(
                        "homeassistant==2026.10.2",
                        {"version": "0.13.373", "requires_dist": ["pytest==8.0.0", dependency]},
                    ),
                    "pytest-homeassistant-custom-component==0.13.373",
                )

    def test_plugin_for_previous_ha_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Matching test plugin unavailable"):
            matching_plugin_requirement(
                "homeassistant==2026.10.3",
                {"version": "0.13.373", "requires_dist": ["homeassistant==2026.10.2"]},
            )

    def test_unpinned_legacy_plugin_and_non_exact_dependencies_are_rejected(self) -> None:
        for dependency in (
            "homeassistant",
            "homeassistant>=2026.10.3",
            "homeassistant==2026.10.*",
            'homeassistant==2026.10.3; python_version < "3.13"',
            "homeassistant==2026.10.3b1",
            "homeassistant[extra]==2026.10.3",
        ):
            with self.subTest(dependency=dependency), self.assertRaises(ValueError):
                matching_plugin_requirement(
                    "homeassistant==2026.10.3",
                    {"version": "0.2.1", "requires_dist": [dependency]},
                )

    def test_invalid_plugin_metadata_is_rejected(self) -> None:
        for info in (
            {},
            {"version": "0.13.374b1", "requires_dist": ["homeassistant==2026.10.3"]},
            {"version": "0.13.374;invalid", "requires_dist": ["homeassistant==2026.10.3"]},
            {"version": "0.13.374", "requires_dist": None},
            {"version": "0.13.374", "requires_dist": [None]},
            {"version": "0.13.374", "requires_dist": []},
            {"version": "0.13.374", "requires_dist": ["homeassistant==2026.10.3"] * 2},
        ):
            with self.subTest(info=info), self.assertRaises(ValueError):
                matching_plugin_requirement("homeassistant==2026.10.3", info)

    def test_non_stable_ha_requirement_is_rejected(self) -> None:
        for requirement in ("", "homeassistant", "homeassistant>=2026.10.3", "homeassistant==2026.10.3b1"):
            with self.subTest(requirement=requirement), self.assertRaises(ValueError):
                matching_plugin_requirement(
                    requirement,
                    {"version": "0.13.374", "requires_dist": [requirement]},
                )


class RequirementCommandTest(unittest.TestCase):
    """Shell musí dostat použitelný pin, nebo chybu bez výstupu pro pip."""

    def test_ha_command_keeps_exact_stable_requirement(self) -> None:
        output = io.StringIO()
        with patch(
            "scripts.latest_ha_requirement._read_info", return_value={"version": "2026.10.3"}
        ), redirect_stdout(output):
            self.assertEqual(main([]), 0)
        self.assertEqual(output.getvalue(), "homeassistant==2026.10.3\n")

    def test_plugin_command_does_not_resolve_ha_again(self) -> None:
        output = io.StringIO()
        with patch(
            "scripts.latest_ha_requirement._read_info",
            return_value={"version": "0.13.373", "requires_dist": ["homeassistant==2026.10.2"]},
        ) as read_info, redirect_stdout(output):
            self.assertEqual(main(["--test-plugin-for", "homeassistant==2026.10.2"]), 0)
        read_info.assert_called_once_with("pytest-homeassistant-custom-component")
        self.assertEqual(output.getvalue(), "pytest-homeassistant-custom-component==0.13.373\n")

    def test_missing_matching_plugin_is_an_explicit_failure(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        with patch(
            "scripts.latest_ha_requirement._read_info",
            return_value={"version": "0.13.373", "requires_dist": ["homeassistant==2026.10.2"]},
        ), redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(main(["--test-plugin-for", "homeassistant==2026.10.3"]), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("Matching test plugin unavailable", errors.getvalue())
        self.assertIn("Full HA fixture tests remain unverified", errors.getvalue())

    def test_network_error_is_a_failure_without_installable_output(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        with patch(
            "scripts.latest_ha_requirement._read_info", side_effect=URLError("offline")
        ), redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(main([]), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("offline", errors.getvalue())
