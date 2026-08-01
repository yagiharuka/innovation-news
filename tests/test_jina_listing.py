import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect.py"
SPEC = importlib.util.spec_from_file_location("collector_jina_listing", MODULE_PATH)
collector = importlib.util.module_from_spec(SPEC)
sys.modules["collector_jina_listing"] = collector
assert SPEC.loader is not None
SPEC.loader.exec_module(collector)


class FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.status_code = 200
        self.url = "https://r.jina.ai/http://agency.example/news"

    def raise_for_status(self):
        return None


class FakeSession:
    def get(self, url, **kwargs):
        self.last_url = url
        return FakeResponse(
            "Title: Official news\\n"
            "Published Time: 2026-08-01T00:00:00Z\\n"
            "[National AI research funding program]"
            "(https://agency.example/news/2026/07/31/ai-funding)\\n"
            "July 31, 2026 — Government launches an AI research program.\\n"
            "[Untrusted repost]"
            "(https://unknown-blog.example/news/2026/07/31/repost)\\n"
        )


class JinaListingSourceTests(unittest.TestCase):
    def test_retains_only_first_party_original_article_urls(self):
        source = {
            "active": True,
            "name": "Official Agency",
            "organization": "Official Agency",
            "source_type": "Government",
            "region": "Global",
            "country": "Global",
            "category": "AI research funding and innovation policy",
            "priority": 5,
            "fetch_mode": "jina_listing",
            "proxy_listing_url": (
                "https://r.jina.ai/http://agency.example/news"
            ),
            "publisher_domain": "agency.example",
            "include_link_patterns": ["/news/"],
            "daily_item_limit": 8,
            "native_feed": False,
        }
        items, result = collector.fetch_jina_listing_source(
            FakeSession(),
            source,
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0]["url"],
            "https://agency.example/news/2026/07/31/ai-funding",
        )
        self.assertNotIn("r.jina.ai", items[0]["url"])
        self.assertIn("original URL retained", items[0]["discovery_method"])

    def test_reader_transport_does_not_replace_original_identity(self):
        self.assertEqual(
            collector.jina_reader_url(
                "https://agency.example/news/item?id=7"
            ),
            "https://r.jina.ai/http://agency.example/news/item?id=7",
        )


if __name__ == "__main__":
    unittest.main()
