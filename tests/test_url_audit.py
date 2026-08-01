import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit_urls.py"
SPEC = importlib.util.spec_from_file_location("url_audit", MODULE_PATH)
url_audit = importlib.util.module_from_spec(SPEC)
sys.modules["url_audit"] = url_audit
assert SPEC.loader is not None
SPEC.loader.exec_module(url_audit)


class UrlAuditTests(unittest.TestCase):
    def test_only_404_and_410_are_classified_as_dead(self):
        self.assertEqual(url_audit.classify_status(404), "dead")
        self.assertEqual(url_audit.classify_status(410), "dead")
        self.assertEqual(url_audit.classify_status(403), "access_restricted")
        self.assertEqual(url_audit.classify_status(429), "rate_limited")
        self.assertEqual(url_audit.classify_status(503), "transient_error")
        self.assertEqual(url_audit.classify_status(200), "live")


if __name__ == "__main__":
    unittest.main()
