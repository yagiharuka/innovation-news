import importlib.util
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
