import importlib.util
import sys
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import collect  # noqa: E402


MODULE_PATH = SCRIPTS / "review_fresh.py"
SPEC = importlib.util.spec_from_file_location("review_fresh", MODULE_PATH)
review_fresh = importlib.util.module_from_spec(SPEC)
sys.modules["review_fresh"] = review_fresh
assert SPEC.loader is not None
SPEC.loader.exec_module(review_fresh)


class FreshReviewTests(unittest.TestCase):
    def test_selector_keeps_strict_oldest_first_order(self):
        now = datetime(2026, 7, 31, tzinfo=timezone.utc)

        def pending(item_id, hours_ago, published_at):
            return {
                "canonical_id": item_id,
                "first_seen": (now - timedelta(hours=hours_ago)).isoformat(),
                "published_at": published_at,
                "scope_review_version": "old-version",
            }

        priority_items = [
            pending("new-1h", 1, "2026-07-01T00:00:00Z"),
            pending("old-30h", 30, "2026-07-31T00:00:00Z"),
            pending("oldest-36h", 36, "2026-07-30T00:00:00Z"),
        ]

        selected = review_fresh.select_fresh_only(
            list(reversed(priority_items)),
            list(reversed(priority_items)),
            limit=2,
            balanced=True,
            priority_ids={"new-1h"},
        )

        self.assertEqual(
            [item["canonical_id"] for item in selected],
            ["oldest-36h", "old-30h"],
        )

    def test_completed_empty_queue_refreshes_checkpoint(self):
        checked_at = datetime(2026, 8, 3, 0, 5, tzinfo=timezone.utc)
        state = {
            "status": "completed",
            "review_version": collect.TECH_SCOPE_REVIEW_VERSION,
            "updated_at": "2026-08-01T00:00:00Z",
            "pending_in_window": 0,
            "pending_priority_36h": 0,
        }
        with (
            mock.patch.object(collect, "load_review_state", return_value=state),
            mock.patch.object(collect, "now_utc", return_value=checked_at),
            mock.patch.object(
                collect,
                "load_public_payload",
                return_value={"items": [{"id": "published"}]},
            ),
            mock.patch.object(collect, "save_review_state") as save_state,
        ):
            review_fresh.refresh_completed_checkpoint()

        saved = save_state.call_args.args[0]
        self.assertEqual(saved["updated_at"], "2026-08-03T00:05:00Z")
        self.assertEqual(saved["pending_priority_36h"], 0)
        self.assertEqual(saved["public_items"], 1)

    def test_completed_priority_queue_ignores_historical_pending_items(self):
        checked_at = datetime(2026, 8, 12, 0, 5, tzinfo=timezone.utc)
        state = {
            "status": "in_progress",
            "review_version": collect.TECH_SCOPE_REVIEW_VERSION,
            "updated_at": "2026-08-12T00:00:00Z",
            "pending_in_window": 2,
            "pending_priority_36h": 0,
        }
        with (
            mock.patch.object(collect, "load_review_state", return_value=state),
            mock.patch.object(collect, "now_utc", return_value=checked_at),
            mock.patch.object(
                collect,
                "load_public_payload",
                return_value={"items": []},
            ),
            mock.patch.object(collect, "save_review_state") as save_state,
        ):
            review_fresh.refresh_completed_checkpoint()

        saved = save_state.call_args.args[0]
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["backlog_status"], "in_progress")
        self.assertEqual(saved["pending_in_window"], 2)
        self.assertEqual(saved["pending_priority_36h"], 0)

    def test_review_statuses_separate_priority_and_backlog(self):
        priority_status, backlog_status = collect.review_state_statuses(
            2,
            0,
            {"rate_limited": False, "request_budget_reached": False},
        )

        self.assertEqual(priority_status, "completed")
        self.assertEqual(backlog_status, "in_progress")


if __name__ == "__main__":
    unittest.main()
