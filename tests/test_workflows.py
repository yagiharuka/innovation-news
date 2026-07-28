import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_workflow_yaml_is_valid(self):
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            with self.subTest(path=path.name):
                payload = yaml.load(
                    path.read_text(encoding="utf-8"),
                    Loader=yaml.BaseLoader,
                )
                self.assertIsInstance(payload, dict)
                self.assertIn("jobs", payload)

    def test_backlog_review_has_one_30_minute_schedule(self):
        daily = (ROOT / ".github" / "workflows" / "daily.yml").read_text(
            encoding="utf-8"
        )
        review = (
            ROOT / ".github" / "workflows" / "review-backlog.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("7,22,37,52", daily)
        self.assertIn('cron: "7,37 * * * *"', review)
        self.assertEqual(review.count("cron:"), 1)

    def test_scheduled_review_uses_eight_item_batches_and_global_gate(self):
        review = (
            ROOT / ".github" / "workflows" / "review-backlog.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("python3 scripts/review_gate.py", review)
        self.assertIn("|| '16'", review)
        self.assertIn('JAPANESE_SUMMARY_BATCH_SIZE: "8"', review)
        self.assertIn("inputs.force", review)
        self.assertIn('--request-budget "$GATE_REQUEST_BUDGET"', review)


if __name__ == "__main__":
    unittest.main()
