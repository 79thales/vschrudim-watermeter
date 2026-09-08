"""Unit tests for bounded, privacy-safe download-attempt persistence."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

PACKAGE = "vschrudim_watermeter_attempt_tests"
root = Path(__file__).parents[1] / "custom_components" / "vschrudim_watermeter"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(root)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(f"{PACKAGE}.attempts", root / "attempts.py")
attempts = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = attempts
spec.loader.exec_module(attempts)


class DownloadAttemptTests(unittest.TestCase):
    def _attempt(self, index: int):
        return attempts.DownloadAttempt(
            started_at=f"2026-09-07T12:{index % 60:02d}:00+02:00",
            finished_at=f"2026-09-07T12:{index % 60:02d}:01+02:00",
            duration_ms=200,
            result="success",
            source="html_table",
            reading_count=10,
            latest_timestamp="2026-09-06T15:00:00",
            missing_hourly_readings=0,
            portal_page_features=("html_table", "webforms_form"),
            reading_quality_flags=("meter_state_decreased",),
        )

    def test_history_is_bounded(self):
        records = []
        for index in range(attempts.MAX_DOWNLOAD_ATTEMPTS + 7):
            records = attempts.append_attempt(records, self._attempt(index))

        self.assertEqual(len(records), attempts.MAX_DOWNLOAD_ATTEMPTS)
        self.assertEqual(records[0], self._attempt(7))

    def test_load_ignores_malformed_and_sanitizes_sensitive_text(self):
        loaded = attempts.load_attempt_history(
            [
                {"not": "an attempt"},
                {
                    **self._attempt(1).as_dict(),
                    "result": "failed",
                    "error": "token=secret https://example.invalid/a?document=secret",
                },
            ]
        )

        self.assertEqual(len(loaded), 1)
        self.assertNotIn("secret", loaded[0].error or "")
        self.assertNotIn("example.invalid", loaded[0].error or "")

    def test_round_trip_preserves_only_safe_fields(self):
        original = self._attempt(3)
        loaded = attempts.load_attempt_history([original.as_dict()])

        self.assertEqual(loaded, [original])

    def test_serialization_sanitizes_error_defensively(self):
        original = attempts.DownloadAttempt(
            started_at="2026-09-07T12:00:00+02:00",
            finished_at="2026-09-07T12:00:01+02:00",
            duration_ms=200,
            result="failed",
            error="password=credential-token-73921 https://example.invalid/private",
        )

        serialized = original.as_dict()

        self.assertNotIn("credential-token-73921", serialized["error"])
        self.assertNotIn("example.invalid", serialized["error"])

    def test_serialization_discards_unknown_page_profile_values(self):
        original = self._attempt(4)
        serialized = original.as_dict()
        serialized["portal_page_features"] = ["html_table", "private-control"]
        serialized["reading_quality_flags"] = [
            "meter_state_decreased",
            "unexpected-portal-value",
        ]

        restored = attempts.DownloadAttempt.from_dict(serialized)

        self.assertEqual(restored.portal_page_features, ("html_table",))
        self.assertEqual(restored.reading_quality_flags, ("meter_state_decreased",))
