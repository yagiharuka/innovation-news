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

    def test_fresh_review_has_one_30_minute_schedule(self):
        daily = (ROOT / ".github" / "workflows" / "daily.yml").read_text(
            encoding="utf-8"
        )
        review = (
            ROOT / ".github" / "workflows" / "review-backlog.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("7,22,37,52", daily)
        self.assertIn('cron: "7,37 * * * *"', review)
        self.assertEqual(review.count("cron:"), 1)
        self.assertIn("scripts/review_fresh.py", review)
        self.assertIn("scripts/fresh_review_gate.py", review)

    def test_scheduled_review_uses_six_item_batches_and_global_gate(self):
        review = (
            ROOT / ".github" / "workflows" / "review-backlog.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("python3 scripts/fresh_review_gate.py", review)
        self.assertIn("|| '12'", review)
        self.assertIn("&& '100' || '12'", review)
        self.assertIn('JAPANESE_SUMMARY_BATCH_SIZE: "6"', review)
        self.assertIn("github.event_name != 'schedule'", review)
        self.assertIn("inputs.force", review)
        self.assertIn("github.event_name == 'push' ||", review)
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
        self.assertIn("scripts/review_fresh.py", daily)
        self.assertIn("scripts/assert_fresh_review_complete.py", daily)
        self.assertIn("JAPANESE_SUMMARY_REQUEST_BUDGET=25", daily)
        self.assertIn("JAPANESE_SUMMARY_REQUEST_BUDGET=85", daily)

    def test_weekly_sources_run_after_the_morning_brief_with_minimal_review(self):
        weekly = (
            ROOT / ".github" / "workflows" / "weekly.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('cron: "0 2 * * 0"', weekly)
        self.assertIn('JAPANESE_SUMMARY_REQUEST_BUDGET: "1"', weekly)
        self.assertIn("workflow_dispatch:", weekly)

    def test_historical_review_runs_only_outside_the_morning_window(self):
        history = (
            ROOT / ".github" / "workflows" / "review-history.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('cron: "17 2-17 * * *"', history)
        self.assertIn("scripts/history_review_gate.py", history)
        self.assertIn("scripts/collect.py --review-only", history)
        self.assertIn('".github/manual-history-review-trigger"', history)
        self.assertIn("&& '500' || '150'", history)
        self.assertIn('JAPANESE_SUMMARY_BATCH_SIZE: "10"', history)
        self.assertIn("&& '75' || '15'", history)

    def test_all_completion_gates_use_the_protected_priority_count(self):
        for relative_path in (
            "scripts/fresh_review_gate.py",
            "scripts/history_review_gate.py",
            "scripts/assert_fresh_review_complete.py",
        ):
            with self.subTest(path=relative_path):
                script = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("priority_pending_count", script)

    def test_url_audit_covers_sources_and_published_articles(self):
        workflow = (
            ROOT / ".github" / "workflows" / "url-audit.yml"
        ).read_text(encoding="utf-8")
        script = (ROOT / "scripts" / "audit_urls.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("scripts/audit_urls.py", workflow)
        self.assertIn("docs/data/url_audit.json", workflow)
        self.assertIn('\"proxy_sitemap_url\"', script)
        self.assertIn('"kind": "article"', script)

    def test_oecd_recovery_is_targeted_and_requires_public_coverage(self):
        workflow = (
            ROOT / ".github" / "workflows" / "oecd-recovery.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('".github/manual-oecd-collection-trigger"', workflow)
        self.assertIn('--source "OECD Newsroom"', workflow)
        self.assertIn('--source "OECD STI Topic Hubs"', workflow)
        self.assertIn("--backfill", workflow)
        self.assertIn("Strict review published no OECD item", workflow)
        self.assertIn("OPENAI_API_KEY", workflow)
        self.assertIn("OECD strict review is incomplete", workflow)
        self.assertIn('row.get("last_checked_at") == checked_at', workflow)
        self.assertIn('hostname or "").lower() == "oecd.org"', workflow)

    def test_url_audit_serializes_writes_and_always_uploads_diagnostics(self):
        workflow = (
            ROOT / ".github" / "workflows" / "url-audit.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("group: innovation-news-collection", workflow)
        self.assertIn("- name: Upload full audit report\n        if: always()", workflow)


if __name__ == "__main__":
    unittest.main()
