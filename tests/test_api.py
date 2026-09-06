"""Tests with anonymized, static portal export examples."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

# Parser tests do not make HTTP calls; provide the minimal runtime module when
# executing outside Home Assistant's dependency environment.
aiohttp = types.ModuleType("aiohttp")
aiohttp.ClientSession = object
aiohttp.ClientError = OSError
sys.modules.setdefault("aiohttp", aiohttp)

PACKAGE = "vschrudim_watermeter"
root = Path(__file__).parents[1] / "custom_components" / PACKAGE
package = types.ModuleType(PACKAGE); package.__path__ = [str(root)]; sys.modules[PACKAGE] = package
for name in ("const", "models", "calculation", "api"):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", root / f"{name}.py")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
api = sys.modules[f"{PACKAGE}.api"]

class ApiParserTests(unittest.TestCase):
    def test_parse_csv_and_deduplicates_timestamp(self):
        readings = api.parse_readings_csv("MERIDLO;CAS;STAV\nA;01.01.2026 00:00;10,000\nA;01.01.2026 01:00;10,125\nA;01.01.2026 01:00;10,130\n")
        self.assertEqual([(item.timestamp.hour, item.meter_state_m3) for item in readings], [(0, 10.0), (1, 10.13)])

    def test_parse_csv_rejects_changed_header(self):
        with self.assertRaises(api.VsChrudimProtocolError):
            api.parse_readings_csv("foo;bar\n1;2")

    def test_parse_consumption_places(self):
        html = '<table id="x_gvConsumptionPlaces"><tr><th>x</th></tr><tr><td>123</td><td>456</td><td>Example 1</td><td>C-1</td><td>v1</td></tr></table>'
        place = api.parse_consumption_places(html)[0]
        self.assertEqual(place.evidence_number, "123")
        self.assertEqual(place.address, "example 1")
