import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


class FrontendFilterTests(unittest.TestCase):
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
            "政府・公的機関",
            "国際機関",
            "政策シンクタンク",
            "企業公式",
            "産業団体・標準化機関",
            "主要メディア",
            "学術・研究情報",
        ]
        for label in labels:
            with self.subTest(label=label):
                self.assertEqual(APP_JS.count(f'label: "{label}"'), 1)
        self.assertIn("<span>情報源区分</span>", INDEX_HTML)
        self.assertNotIn("<span>情報源の種類</span>", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
