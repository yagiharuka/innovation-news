import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


class FrontendFilterTests(unittest.TestCase):
    def test_requested_homepage_copy_is_removed(self):
        self.assertNotIn(
            "政府・国際機関、企業公式、主要メディア、著名政策研究機関",
            INDEX_HTML,
        )
        self.assertNotIn("初回収録はイノベーション政策が過去1年", INDEX_HTML)

    def test_openai_news_is_retired_from_config_and_hidden_from_stale_data(self):
        config = json.loads(
            (ROOT / "config" / "sources.json").read_text(encoding="utf-8")
        )
        configured_sources = {
            source["name"]
            for source in config["sources"]
            if source.get("active", True)
        }
        self.assertNotIn("OpenAI News", configured_sources)
        self.assertIn(
            'const RETIRED_SOURCE_NAMES = new Set(["OpenAI News"])',
            APP_JS,
        )
        self.assertIn(
            "items.filter((item) => !RETIRED_SOURCE_NAMES.has(item.source))",
            APP_JS,
        )

    def test_article_frame_uses_requested_japanese_labels(self):
        self.assertIn('"Technology Innovation": "技術"', APP_JS)
        self.assertIn('"Innovation Policy": "イノベーション政策"', APP_JS)
        self.assertNotIn("技術イノベーション", APP_JS)

    def test_all_active_source_types_have_one_source_group(self):
        config = json.loads(
            (ROOT / "config" / "sources.json").read_text(encoding="utf-8")
        )
        source_types = {
            source["source_type"]
            for source in config["sources"]
            if source.get("active", True)
        }
        expected_source_types = {
            "Government",
            "Intergovernmental",
            "Policy Institute",
            "Official Company",
            "Industry Association",
            "Major Media",
            "Scientific Publication",
            "Journal Article",
            "Conference Paper",
            "Preprint",
        }
        self.assertEqual(source_types, expected_source_types)
        source_group_block = APP_JS.split(
            "const SOURCE_GROUPS = [", 1
        )[1].split("const SOURCE_GROUP_BY_TYPE", 1)[0]
        for source_type in source_types:
            with self.subTest(source_type=source_type):
                self.assertEqual(
                    source_group_block.count(f'"{source_type}"'),
                    1,
                )

    def test_source_groups_are_japanese_and_mece(self):
        labels = [
            "政府系機関（各国政府・政府間機関）",
            "事業会社（公式情報）",
            "非政府調査・研究機関",
            "会員制団体（業界・専門・標準化）",
            "学術情報（大学・論文誌・論文DB）",
            "報道機関",
        ]
        for label in labels:
            with self.subTest(label=label):
                self.assertEqual(APP_JS.count(f'label: "{label}"'), 1)
        self.assertIn(
            'sourceTypes: ["Government", "Intergovernmental"]',
            APP_JS,
        )
        self.assertNotIn('label: "国際機関"', APP_JS)
        self.assertNotIn('label: "政策シンクタンク"', APP_JS)
        self.assertNotIn('label: "産業団体・標準化機関"', APP_JS)
        self.assertIn("<span>情報源の主分類</span>", INDEX_HTML)
        self.assertNotIn("<span>情報源区分</span>", INDEX_HTML)
        self.assertNotIn("<span>情報源の種類</span>", INDEX_HTML)

    def test_legacy_institution_overrides_are_exclusive(self):
        override_block = APP_JS.split(
            "const SOURCE_GROUP_BY_SOURCE = new Map([", 1
        )[1].split("]);", 1)[0]
        expected = {
            "JST CRDS STI Policy Reports": "public",
            "KISTEP": "public",
            "STEPI": "public",
            "Technology Innovation Institute": "public",
            "Science Japan": "public",
            "日本人工知能学会": "membership",
            "Japan Space Systems": "research",
        }
        config = json.loads(
            (ROOT / "config" / "sources.json").read_text(encoding="utf-8")
        )
        configured_sources = {
            source["name"]
            for source in config["sources"]
            if source.get("active", True)
        }
        valid_groups = {
            "public",
            "company",
            "research",
            "membership",
            "academic",
            "media",
        }
        for source, group in expected.items():
            with self.subTest(source=source):
                self.assertIn(source, configured_sources)
                self.assertIn(group, valid_groups)
                self.assertEqual(
                    override_block.count(f'["{source}", "{group}"]'),
                    1,
                )


if __name__ == "__main__":
    unittest.main()
