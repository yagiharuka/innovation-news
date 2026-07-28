import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "review_gate.py"
SPEC = importlib.util.spec_from_file_location("review_gate", MODULE_PATH)
review_gate = importlib.util.module_from_spec(SPEC)
sys.modules["review_gate"] = review_gate
assert SPEC.loader is not None
SPEC.loader.exec_module(review_gate)


class ReviewGateTests(unittest.TestCase):
    NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)

    def test_counts_all_model_requests_not_only_backlog_runs(self):
        result = review_gate.evaluate_review_gate(
            {
                "runs": [
                    {
                        "run_at": "2026-07-28T10:00:00Z",
                        "summary_requests": 120,
                        "note": "Daily update",
                    },
                    {
                        "run_at": "2026-07-28T11:00:00Z",
                        "summary_requests": 25,
                        "note": "Review backlog",
                    },
                ]
            },
            {
                "updated_at": "2026-07-28T11:00:00Z",
                "pending_in_window": 100,
            },
            now=self.NOW,
        )

        self.assertEqual(result["all_requests_used"], 145)
        self.assertEqual(result["backlog_requests_used"], 25)
        self.assertFalse(result["quota_available"])
        self.assertFalse(result["should_run"])

    def test_retry_after_blocks_until_the_reported_time(self):
        state = {
            "updated_at": "2026-07-28T11:00:00Z",
            "pending_in_window": 100,
            "rate_limited": True,
            "rate_limited_at": "2026-07-28T11:30:00Z",
            "retry_after": "3600",
        }
        result = review_gate.evaluate_review_gate(
            {"runs": []},
            state,
            now=self.NOW,
            force=True,
        )

        self.assertFalse(result["cooldown_complete"])
        self.assertEqual(
            result["retry_blocked_until"],
            "2026-07-28T12:31:00+00:00",
        )
        self.assertFalse(result["should_run"])

    def test_legacy_runs_before_request_logging_do_not_block_forever(self):
        result = review_gate.evaluate_review_gate(
            {
                "runs": [
                    {
                        "run_at": "2026-07-28T09:00:00Z",
                        "summaries_generated": 10,
                        "note": "Daily update",
                    },
                    {
                        "run_at": "2026-07-28T10:00:00Z",
                        "summary_requests": 2,
                        "note": "Review backlog",
                    },
                ]
            },
            {
                "updated_at": "2026-07-28T11:00:00Z",
                "pending_in_window": 100,
            },
            now=self.NOW,
        )

        self.assertTrue(result["data_valid"])
        self.assertEqual(result["legacy_unmetered"], 1)
        self.assertEqual(result["all_requests_used"], 2)

    def test_manual_twenty_request_budget_is_reserved_by_gate(self):
        result = review_gate.evaluate_review_gate(
            {
                "runs": [
                    {
                        "run_at": "2026-07-28T10:00:00Z",
                        "summary_requests": 110,
                        "note": "Daily update",
                    }
                ]
            },
            {
                "updated_at": "2026-07-28T11:00:00Z",
                "pending_in_window": 100,
            },
            now=self.NOW,
            force=True,
            next_request_budget=20,
        )

        self.assertFalse(result["quota_available"])
        self.assertFalse(result["should_run"])

    def test_completed_long_cooldown_starts_a_fresh_quota_window(self):
        result = review_gate.evaluate_review_gate(
            {
                "runs": [
                    {
                        "run_at": "2026-07-28T06:00:00Z",
                        "summary_requests": 145,
                        "summary_rate_limited": True,
                        "summary_retry_after": "7200",
                        "note": "Daily update",
                    },
                    {
                        "run_at": "2026-07-28T09:00:00Z",
                        "summary_requests": 2,
                        "note": "Review backlog",
                    },
                ]
            },
            {
                "updated_at": "2026-07-28T11:00:00Z",
                "pending_in_window": 100,
            },
            now=self.NOW,
        )

        self.assertEqual(result["all_requests_used"], 2)
        self.assertEqual(
            result["window_start"],
            "2026-07-28T08:00:00+00:00",
        )
        self.assertTrue(result["should_run"])

    def test_persisted_quota_reset_survives_a_successful_review_state(self):
        result = review_gate.evaluate_review_gate(
            {
                "runs": [
                    {
                        "run_at": "2026-07-28T06:00:00Z",
                        "summary_requests": 145,
                        "note": "Daily update",
                    },
                    {
                        "run_at": "2026-07-28T09:00:00Z",
                        "summary_requests": 2,
                        "note": "Review backlog",
                    },
                ]
            },
            {
                "updated_at": "2026-07-28T11:00:00Z",
                "pending_in_window": 100,
                "rate_limited": False,
                "quota_window_reset_at": "2026-07-28T08:00:00Z",
            },
            now=self.NOW,
        )

        self.assertEqual(result["all_requests_used"], 2)
        self.assertEqual(
            result["window_start"],
            "2026-07-28T08:00:00+00:00",
        )
        self.assertTrue(result["should_run"])

    def test_force_does_not_bypass_quota_safeguards(self):
        result = review_gate.evaluate_review_gate(
            {
                "runs": [
                    {
                        "run_at": "2026-07-28T11:30:00Z",
                        "summary_requests": 96,
                        "note": "Review backlog",
                    }
                ]
            },
            {
                "updated_at": "2026-07-28T11:55:00Z",
                "pending_in_window": 100,
            },
            now=self.NOW,
            force=True,
        )

        self.assertTrue(result["checkpoint_due"])
        self.assertFalse(result["quota_available"])
        self.assertFalse(result["should_run"])

    def test_zero_pending_items_skips_review(self):
        result = review_gate.evaluate_review_gate(
            {"runs": []},
            {
                "updated_at": "2026-07-28T11:00:00Z",
                "pending_in_window": 0,
            },
            now=self.NOW,
        )

        self.assertFalse(result["pending_available"])
        self.assertFalse(result["should_run"])

    def test_missing_run_log_fails_closed(self):
        result = review_gate.evaluate_review_gate(
            {},
            {
                "updated_at": "2026-07-28T11:00:00Z",
                "pending_in_window": 100,
            },
            now=self.NOW,
        )

        self.assertFalse(result["data_valid"])
        self.assertFalse(result["should_run"])


if __name__ == "__main__":
    unittest.main()
