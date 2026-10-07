"""Tests for exact latest-stable requirement selection without network access."""

from __future__ import annotations

import unittest

from scripts.latest_ha_requirement import latest_requirement


class LatestHomeAssistantRequirementTest(unittest.TestCase):
    def test_exact_stable_version_is_used(self) -> None:
        self.assertEqual(latest_requirement({"version": "2026.9.4"}), "homeassistant==2026.9.4")
        self.assertEqual(latest_requirement({"version": "2027.1.0"}), "homeassistant==2027.1.0")

    def test_beta_and_invalid_metadata_are_rejected(self) -> None:
        for version in ("2026.10.0b2", "2026.10.0rc1", "latest", None, "", "2026.9.4;invalid"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                latest_requirement({"version": version})
