import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_workflow_files_have_required_sections(self):
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            with self.subTest(path=path.name):
                workflow = path.read_text(encoding="utf-8")
                self.assertIn("name:", workflow)
                self.assertIn("on:", workflow)
                self.assertIn("jobs:", workflow)

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

    def test_scheduled_review_uses_six_item_batches_and_global_gate(self):
        review = (
            ROOT / ".github" / "workflows" / "review-backlog.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("python3 scripts/review_gate.py", review)
        self.assertIn("|| '12'", review)
        self.assertIn("&& '100' || '12'", review)
        self.assertIn('JAPANESE_SUMMARY_BATCH_SIZE: "6"', review)
        self.assertIn("github.event_name != 'schedule'", review)
        self.assertIn("inputs.force", review)
        self.assertIn('--request-budget "$GATE_REQUEST_BUDGET"', review)

    def test_backlog_review_has_a_manual_push_trigger(self):
        review = (
            ROOT / ".github" / "workflows" / "review-backlog.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('".github/manual-review-trigger"', review)
        self.assertIn("github.event_name == 'workflow_dispatch'", review)

    def test_code_push_runs_verification_without_a_model_collection(self):
        daily = (ROOT / ".github" / "workflows" / "daily.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("github.event_name == 'workflow_dispatch'", daily)
        self.assertIn("verify:", daily)
        self.assertIn("if: github.event_name == 'push'", daily)
        self.assertIn("python -m unittest discover -s tests", daily)
        self.assertNotIn("github.event_name != 'schedule'", daily)
        self.assertIn('cron: "0 19 * * *"', daily)


if __name__ == "__main__":
    unittest.main()
