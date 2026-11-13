"""Kontrola veřejných HA rozhraní generátoru na právě instalovaném core."""

import importlib.util
import inspect
import unittest
from types import SimpleNamespace

HAS_HA = importlib.util.find_spec("homeassistant") is not None


@unittest.skipUnless(HAS_HA, "Home Assistant není v tomto prostředí nainstalován")
class DashboardHACompatibilityTests(unittest.TestCase):
    def test_dashboard_handlers_require_admin_and_recorder_api_is_available(self):
        """Ověřit reálné dekorátory a signatury bez síťového připojení."""
        from homeassistant.components.recorder.statistics import (
            async_list_statistic_ids,
            statistics_during_period,
        )
        from homeassistant.const import EVENT_COMPONENT_LOADED
        from homeassistant.exceptions import Unauthorized
        from homeassistant.helpers.device_registry import async_entries_for_config_entry

        from custom_components.vschrudim_watermeter.dashboard_api import (
            websocket_compare,
            websocket_preview,
            websocket_sources,
        )

        self.assertTrue(callable(async_entries_for_config_entry))
        self.assertEqual(EVENT_COMPONENT_LOADED, "component_loaded")
        self.assertIn(
            "statistic_type", inspect.signature(async_list_statistic_ids).parameters
        )
        self.assertEqual(
            list(inspect.signature(statistics_during_period).parameters)[:7],
            [
                "hass",
                "start_time",
                "end_time",
                "statistic_ids",
                "period",
                "units",
                "types",
            ],
        )
        for handler in (websocket_compare, websocket_preview, websocket_sources):
            for user in (None, SimpleNamespace(is_admin=False)):
                with (
                    self.subTest(handler=handler.__name__, user=user),
                    self.assertRaises(Unauthorized),
                ):
                    handler(None, SimpleNamespace(user=user), {"id": 1})


if __name__ == "__main__":
    unittest.main()
