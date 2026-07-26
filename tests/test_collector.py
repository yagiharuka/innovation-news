import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect.py"
SPEC = importlib.util.spec_from_file_location("collector", MODULE_PATH)
collector = importlib.util.module_from_spec(SPEC)
sys.modules["collector"] = collector
assert SPEC.loader is not None
SPEC.loader.exec_module(collector)


class CollectorTests(unittest.TestCase):
    def test_canonicalize_url_removes_tracking(self):
        actual = collector.canonicalize_url(
            "https://www.example.com/story/?utm_source=rss&b=2&a=1#section"
        )
        self.assertEqual(actual, "https://example.com/story?a=1&b=2")

    def test_topic_classification_is_multilabel(self):
        topics = collector.classify_topics(
            "Government launches a national quantum computing funding programme "
            "for semiconductor research."
        )
        self.assertIn("Innovation Policy", topics)
        self.assertIn("Quantum", topics)
        self.assertIn("Semiconductors & Telecom", topics)

    def test_region_classification_overrides_global_default(self):
        region = collector.classify_region(
            "The European Commission announced a new AI investment initiative.",
            "Global",
        )
        self.assertEqual(region, "EU & Europe")

    def test_deduplication_uses_url_and_title(self):
        existing = [
            {
                "canonical_id": "old-id",
                "url": "https://example.com/a",
                "title": "Government launches national quantum strategy",
            }
        ]
        duplicate_url = {
            "canonical_id": "new-id",
            "canonical_url": "https://example.com/a",
            "title": "A different title",
            "title_fingerprint": collector.title_fingerprint("A different title"),
        }
        duplicate_title = {
            "canonical_id": "new-id-2",
            "canonical_url": "https://example.com/b",
            "title": "Government launches national quantum strategy",
            "title_fingerprint": collector.title_fingerprint(
                "Government launches national quantum strategy"
            ),
        }
        unique = {
            "canonical_id": "new-id-3",
            "canonical_url": "https://example.com/c",
            "title": "New fusion pilot reaches milestone",
            "title_fingerprint": collector.title_fingerprint(
                "New fusion pilot reaches milestone"
            ),
        }
        added, skipped = collector.deduplicate(
            [duplicate_url, duplicate_title, unique], existing
        )
        self.assertEqual(skipped, 2)
        self.assertEqual([item["canonical_id"] for item in added], ["new-id-3"])


if __name__ == "__main__":
    unittest.main()
