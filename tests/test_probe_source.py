import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "probe_source_test_module", SCRIPTS / "probe_source.py"
)
probe_source = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(probe_source)


class ProbeSourceTests(unittest.TestCase):
    def test_critical_probe_requires_nonzero_seen_and_kept(self):
        self.assertFalse(probe_source.probe_is_healthy("ok", 0, 0, 1, 1))
        self.assertFalse(probe_source.probe_is_healthy("ok", 20, 0, 1, 1))
        self.assertTrue(probe_source.probe_is_healthy("ok", 20, 3, 1, 1))

    def test_noncritical_probe_can_accept_a_healthy_zero_result(self):
        self.assertTrue(probe_source.probe_is_healthy("ok", 0, 0))
        self.assertFalse(probe_source.probe_is_healthy("error", 20, 3))


if __name__ == "__main__":
    unittest.main()
