import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect.py"
SPEC = importlib.util.spec_from_file_location("collector", MODULE_PATH)
collector = importlib.util.module_from_spec(SPEC)
sys.modules["collector"] = collector
assert SPEC.loader is not None
SPEC.loader.exec_module(collector)


class CollectorTests(unittest.TestCase):
    def test_taxonomy_has_eight_technology_topics_and_separate_policy_axis(self):
        self.assertEqual(len(collector.TOPIC_KEYWORDS), 8)
        self.assertIn("Space", collector.TOPIC_KEYWORDS)
        self.assertNotIn("Innovation Policy", collector.TOPIC_KEYWORDS)
        self.assertIn("Patents & Intellectual Property", collector.POLICY_AREA_KEYWORDS)

    def test_only_current_reviewed_nonexcluded_items_are_publishable(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "title_ja": "量子技術の新成果",
            "summary_ja": "新しい量子制御手法を実証した。",
            "article_frames": ["Technology Innovation"],
            "topics": ["Quantum"],
        }
        self.assertTrue(collector.is_publishable(item))
        item["status"] = "Excluded"
        self.assertFalse(collector.is_publishable(item))
        self.assertFalse(collector.needs_scope_review(item))
        item["scope_review_version"] = "older-review"
        self.assertTrue(collector.needs_scope_review(item))

    def test_reviewed_topics_require_article_evidence_not_source_category(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "title": "National project accelerates AI-driven scientific discovery",
            "summary": "The programme funds artificial intelligence research infrastructure.",
            "topics": ["Quantum"],
            "topic": "Quantum",
        }
        collector.normalize_reviewed_topics([item])
        self.assertEqual(item["topics"], ["Artificial Intelligence"])
        self.assertEqual(item["topic"], "Artificial Intelligence")

    def test_private_fundraising_is_not_innovation_policy(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "title": "Fusion startup raises JPY 6.06 billion",
            "summary": "The company will accelerate development of its tokamak device.",
            "topics": ["Fusion Energy"],
            "topic": "Fusion Energy",
            "article_frames": ["Innovation Policy"],
            "article_frame": "Innovation Policy",
            "innovation_policy": True,
            "policy_areas": ["R&D Funding & Tax Incentives"],
            "policy_area": "R&D Funding & Tax Incentives",
        }
        collector.normalize_reviewed_policy_axis([item])
        self.assertFalse(item["innovation_policy"])
        self.assertEqual(item["policy_areas"], [])
        self.assertEqual(item["article_frames"], ["Technology Innovation"])

    def test_canonicalize_url_removes_tracking(self):
        actual = collector.canonicalize_url(
            "https://www.example.com/story/?utm_source=rss&b=2&a=1#section"
        )
        self.assertEqual(actual, "https://example.com/story?a=1&b=2")

    def test_topic_classification_is_multilabel(self):
        topics = collector.classify_topics(
            "Government launches a national quantum computing funding programme "
            "for semiconductor research and satellite communications."
        )
        self.assertIn("Quantum", topics)
        self.assertIn("Semiconductors & Telecom", topics)
        self.assertIn("Space", topics)

    def test_policy_classification_is_separate_from_technology_topics(self):
        text = (
            "The government expands its R&D tax credit, launches a national project, "
            "and reforms patent licensing."
        )
        self.assertEqual(collector.classify_topics(text), [])
        policy_areas = collector.classify_policy_areas(text)
        self.assertIn("R&D Funding & Tax Incentives", policy_areas)
        self.assertIn("National Programs & Strategy", policy_areas)
        self.assertIn("Patents & Intellectual Property", policy_areas)

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
            "article_frames": ["Innovation Policy"],
            "innovation_policy": True,
            "policy_areas": ["National Programs & Strategy"],
            "policy_relevance": 4,
        }
        public = collector.public_item(item)
        self.assertEqual(public["title"], item["title_ja"])
        self.assertEqual(public["title_original"], item["title"])
        self.assertEqual(public["summary"], item["summary_ja"])
        self.assertEqual(public["summary_original"], item["summary"])
        self.assertEqual(public["summary_language"], "ja")
        self.assertEqual(public["article_frames"], ["Innovation Policy"])
        self.assertEqual(public["policy_areas"], ["National Programs & Strategy"])

    def test_parse_japanese_summary_response(self):
        raw = json.dumps(
            {
                "items": [
                    {
                        "id": "article-1",
                        "in_scope": True,
                        "topics": ["Quantum"],
                        "is_innovation_policy": True,
                        "policy_areas": [
                            "National Programs & Strategy",
                            "R&D Funding & Tax Incentives",
                        ],
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
        self.assertEqual(parsed["article-1"]["topics"], ["Quantum"])
        self.assertTrue(parsed["article-1"]["is_innovation_policy"])
        self.assertEqual(
            parsed["article-1"]["policy_areas"],
            ["National Programs & Strategy", "R&D Funding & Tax Incentives"],
        )
        self.assertEqual(parsed["article-1"]["policy_relevance"], 5)

    def test_parse_scope_review_excludes_general_news(self):
        raw = json.dumps(
            {
                "items": [
                    {
                        "id": "article-1",
                        "in_scope": False,
                        "topics": [],
                        "is_innovation_policy": False,
                        "policy_areas": [],
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

    def test_parse_policy_only_article_without_technology_topic(self):
        raw = json.dumps(
            {
                "items": [
                    {
                        "id": "article-1",
                        "in_scope": True,
                        "topics": [],
                        "is_innovation_policy": True,
                        "policy_areas": ["Patents & Intellectual Property"],
                        "policy_relevance": 5,
                        "reason": "研究成果の特許化と技術移転制度を直接扱う。",
                        "content_type": "technology_policy",
                        "technical_focus": "大学研究の特許・技術移転制度",
                        "scope_evidence": "大学の特許ライセンス制度を改正する。",
                        "title_ja": "大学特許の技術移転制度を改正",
                        "summary_ja": "政府が大学研究の特許化とライセンス制度を改正する。",
                    }
                ]
            },
            ensure_ascii=False,
        )
        parsed = collector.parse_japanese_summary_response(raw, {"article-1"})
        self.assertTrue(parsed["article-1"]["in_scope"])
        self.assertEqual(parsed["article-1"]["topics"], [])
        self.assertEqual(
            parsed["article-1"]["policy_areas"],
            ["Patents & Intellectual Property"],
        )

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

    def test_build_item_accepts_cross_cutting_innovation_policy(self):
        source = {
            "name": "Policy Office",
            "organization": "Policy Office",
            "source_type": "Government",
            "region": "Asia",
            "country": "Japan",
            "category": "Science, technology and innovation policy",
            "priority": 5,
        }
        item = collector.build_item(
            source,
            "Government expands R&D tax credit and patent licensing",
            "https://example.go.jp/policy/rd-tax-patents",
            "The reform supports research investment and technology transfer.",
            collector.now_utc(),
            collector.now_utc(),
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["topics"], [])
        self.assertTrue(item["innovation_policy"])
        self.assertIn("R&D Funding & Tax Incentives", item["policy_areas"])

    def test_public_history_windows_differ_by_frame(self):
        collected_at = datetime(2026, 7, 26, tzinfo=timezone.utc)
        policy_item = {
            "published_at": "2025-08-01T00:00:00Z",
            "article_frames": ["Innovation Policy"],
        }
        technology_item = {
            "published_at": "2025-08-01T00:00:00Z",
            "article_frames": ["Technology Innovation"],
        }
        recent_technology_item = {
            "published_at": "2026-02-01T00:00:00Z",
            "article_frames": ["Technology Innovation"],
        }
        self.assertTrue(
            collector.item_within_public_window(policy_item, collected_at)
        )
        self.assertFalse(
            collector.item_within_public_window(technology_item, collected_at)
        )
        self.assertTrue(
            collector.item_within_public_window(
                recent_technology_item, collected_at
            )
        )

    def test_openalex_metadata_summary_uses_topics_and_keywords(self):
        summary = collector.openalex_metadata_summary(
            {
                "topics": [{"display_name": "Quantum computing"}],
                "keywords": [
                    {"display_name": "Qubit"},
                    {"display_name": "Quantum computing"},
                ],
            }
        )
        self.assertEqual(
            summary,
            "OpenAlex research topics: Quantum computing; Qubit",
        )

    def test_public_item_includes_academic_metadata(self):
        public = collector.public_item(
            {
                "academic_kind": collector.ACADEMIC_KIND_PREPRINT,
                "review_status": "Not peer reviewed",
                "venue": "arXiv",
                "doi": "https://doi.org/10.1234/example",
                "citation_count": 7,
                "discovery_method": "OpenAlex",
            }
        )
        self.assertEqual(public["academic_kind"], "Preprint")
        self.assertEqual(public["review_status"], "Not peer reviewed")
        self.assertEqual(public["venue"], "arXiv")
        self.assertEqual(public["citation_count"], 7)

    def test_gdelt_datetime_parser(self):
        fallback = datetime(2026, 7, 26, tzinfo=timezone.utc)
        parsed = collector.parse_gdelt_datetime("20260102T030405Z", fallback)
        self.assertEqual(parsed.isoformat(), "2026-01-02T03:04:05+00:00")

    def test_archive_url_scoring_ignores_domain_name(self):
        self.assertEqual(
            collector.archive_url_score(
                "https://technologyreview.com/general/elections",
                "policy",
            ),
            0,
        )
        self.assertGreater(
            collector.archive_url_score(
                "https://example.com/policy/quantum-research-strategy",
                "policy",
            ),
            0,
        )

    def test_config_has_separate_scholarly_kinds(self):
        config = collector.load_config()
        academic_kinds = {
            source.get("academic_kind")
            for source in config["sources"]
            if source.get("fetch_mode") == "openalex"
        }
        self.assertEqual(
            academic_kinds,
            {"Journal Article", "Conference Paper", "Preprint"},
        )


if __name__ == "__main__":
    unittest.main()
