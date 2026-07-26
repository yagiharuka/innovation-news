import importlib.util
import json
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

    def test_public_item_prefers_japanese_summary(self):
        item = {
            "canonical_id": "article-1",
            "title": "New quantum programme announced",
            "title_ja": "量子技術の新プログラムを発表",
            "summary": "The government announced a new programme.",
            "summary_ja": "政府が量子技術を支援する新プログラムを発表した。",
            "policy_relevance": 4,
        }
        public = collector.public_item(item)
        self.assertEqual(public["title"], item["title_ja"])
        self.assertEqual(public["title_original"], item["title"])
        self.assertEqual(public["summary"], item["summary_ja"])
        self.assertEqual(public["summary_original"], item["summary"])
        self.assertEqual(public["summary_language"], "ja")

    def test_parse_japanese_summary_response(self):
        raw = json.dumps(
            {
                "items": [
                    {
                        "id": "article-1",
                        "in_scope": True,
                        "topics": ["Quantum", "Innovation Policy"],
                        "policy_relevance": 5,
                        "reason": "量子研究開発政策を直接扱う。",
                        "content_type": "technology_policy",
                        "technical_focus": "量子技術の研究開発支援",
                        "scope_evidence": "政府が量子研究開発支援を拡充する。",
                        "title_ja": "量子技術の新計画",
                        "summary_ja": "政府が研究開発支援を拡充する。",
                    },
                    {
                        "id": "unknown",
                        "title_ja": "対象外",
                        "summary_ja": "対象外の記事。",
                    },
                ]
            },
            ensure_ascii=False,
        )
        parsed = collector.parse_japanese_summary_response(raw, {"article-1"})
        self.assertEqual(set(parsed), {"article-1"})
        self.assertEqual(parsed["article-1"]["title_ja"], "量子技術の新計画")
        self.assertTrue(parsed["article-1"]["in_scope"])
        self.assertEqual(parsed["article-1"]["topics"], ["Quantum", "Innovation Policy"])
        self.assertEqual(parsed["article-1"]["policy_relevance"], 5)

    def test_parse_scope_review_excludes_general_news(self):
        raw = json.dumps(
            {
                "items": [
                    {
                        "id": "article-1",
                        "in_scope": False,
                        "topics": [],
                        "policy_relevance": 0,
                        "reason": "技術革新ではなく一般的な観光記事。",
                        "content_type": "none",
                        "technical_focus": "",
                        "scope_evidence": "",
                        "title_ja": "",
                        "summary_ja": "",
                    }
                ]
            },
            ensure_ascii=False,
        )
        parsed = collector.parse_japanese_summary_response(raw, {"article-1"})
        self.assertFalse(parsed["article-1"]["in_scope"])
        self.assertEqual(parsed["article-1"]["topics"], [])

    def test_build_item_applies_source_url_allowlist(self):
        source = {
            "name": "Example Technology",
            "organization": "Example",
            "source_type": "Major Media",
            "region": "Asia",
            "country": "Hong Kong",
            "priority": 4,
            "include_url_patterns": ["/tech/"],
        }
        entry = {
            "title": "New AI accelerator architecture",
            "link": "https://example.com/news/general-story",
            "summary": "A new AI accelerator architecture was announced.",
            "published": "2026-07-26T00:00:00Z",
        }
        self.assertIsNone(
            collector.build_item(
                source,
                entry["title"],
                entry["link"],
                entry["summary"],
                collector.now_utc(),
                collector.now_utc(),
            )
        )


if __name__ == "__main__":
    unittest.main()
