import sys
import unittest
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from collection_due import evaluate

class CollectionDueTests(unittest.TestCase):
    def check_due(self, now='2026-10-13T09:00:00+09:00', runs=None, source='2026-10-12T07:57:54+09:00', attempt=None):
        return evaluate({'runs': runs if runs is not None else [{'note':'Daily update','run_at':source}]},
                        {'updated_at':source}, attempt or {}, datetime.fromisoformat(now))[0]

    def test_stale_empty_review_queue_does_not_hide_missing_collection(self):
        self.assertTrue(self.check_due())

    def test_before_start_does_not_collect(self):
        self.assertFalse(self.check_due(now='2026-10-13T03:16:59+09:00'))
        self.assertTrue(self.check_due(now='2026-10-13T03:17:00+09:00'))

    def test_today_persisted_skips_duplicate(self):
        self.assertFalse(self.check_due(source='2026-10-13T03:18:00+09:00'))

    def test_review_weekly_and_targeted_do_not_count(self):
        for record in [{'note':'Review fresh candidates'}, {'note':'Weekly update'},
                       {'note':'Daily update','targeted_sources':['one source']}]:
            self.assertTrue(self.check_due(runs=[{**record,'run_at':'2026-10-13T05:00:00+09:00'}],source='2026-10-13T05:00:00+09:00'))

    def test_unpublished_failure_does_not_count(self):
        self.assertTrue(self.check_due(runs=[{'note':'Daily update','run_at':'2026-10-13T05:00:00+09:00'}]))

    def test_attempt_retry_interval(self):
        self.assertFalse(self.check_due(attempt={'started_at':'2026-10-13T08:00:00+09:00'}))
        self.assertTrue(self.check_due(attempt={'started_at':'2026-10-13T07:00:00+09:00'}))

    def test_future_timestamp_fails_closed(self):
        with self.assertRaises(ValueError):
            self.check_due(source='2026-10-14T03:17:00+09:00')

    def test_manual_retry_keeps_date_deduplication(self):
        now=datetime.fromisoformat('2026-10-13T09:00:00+09:00')
        attempt={'started_at':'2026-10-13T08:00:00+09:00'}
        for stamp, expected in [('2026-10-12T05:00:00+09:00',True),('2026-10-13T05:00:00+09:00',False)]:
            result=evaluate({'runs':[{'note':'Daily update','run_at':stamp}]}, {'updated_at':stamp},attempt,now,manual_retry=True)
            self.assertEqual(result[0],expected)

    def test_weekend_collection_continues(self):
        self.assertTrue(self.check_due(now='2026-10-17T04:00:00+09:00'))

if __name__ == '__main__':
    unittest.main()
