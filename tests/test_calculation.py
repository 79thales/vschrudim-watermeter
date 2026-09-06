from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest

PACKAGE = "vschrudim_watermeter"
root = Path(__file__).parents[1] / "custom_components" / PACKAGE
package = types.ModuleType(PACKAGE); package.__path__ = [str(root)]; sys.modules[PACKAGE] = package
for name in ("models", "calculation"):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", root / f"{name}.py")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
models = sys.modules[f"{PACKAGE}.models"]; calculation = sys.modules[f"{PACKAGE}.calculation"]

class CalculationTests(unittest.TestCase):
    def test_deltas_handle_initial_and_meter_reset(self):
        readings = [models.MeterReading(datetime(2026, 3, 29, 1), 5.0), models.MeterReading(datetime(2026, 3, 29, 3), 5.2), models.MeterReading(datetime(2026, 3, 29, 4), 1.0)]
        self.assertEqual([value for _, value in calculation.consumption_deltas(readings)], [None, 0.2, None])
