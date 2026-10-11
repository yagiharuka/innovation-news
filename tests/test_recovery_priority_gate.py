import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import assert_fresh_review_complete
import collect
import fresh_review_gate
import history_review_gate
import report_review_outcome
import review_fresh
import review_gate


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


def recovery_state(**overrides):
    return {
        "review_version": collect.TECH_SCOPE_REVIEW_VERSION,
        "updated_at": "2026-10-11T11:00:00Z",
        "status": "in_progress",
        "backlog_status": "in_progress",
        "pending_priority_total": 16,
        "pending_priority_36h": 0,
        "pending_recovery_cohort": 16,
        "pending_in_window": 24,
        "reviewed": 2,
        "model_requests": 1,
        **overrides,
    }


class RecoveryPriorityCountTests(unittest.TestCase):
    def test_effective_total_takes_precedence_over_aged_out_freshness(self):
        self.assertEqual(review_gate.priority_pending_count(recovery_state()), 16)

    def test_zero_total_does_not_fall_back_to_legacy_24h(self):
        state = recovery_state(pending_priority_total=0, pending_recovery_cohort=0,
                               pending_fresh_24h=4)
        self.assertEqual(review_gate.priority_pending_count(state), 0)

    def test_subset_counts_may_overlap_or_be_absent(self):
        for state in (
            {"pending_priority_total": 4},
            {"pending_priority_total": 4, "pending_priority_36h": 4,
             "pending_recovery_cohort": 4},
            {"pending_priority_total": 4, "pending_priority_36h": 2,
             "pending_recovery_cohort": 3},
        ):
            with self.subTest(state=state):
                self.assertEqual(review_gate.priority_pending_count(state), 4)

    def test_inconsistent_or_malformed_subset_counts_fail_closed(self):
        for subset in ("pending_priority_36h", "pending_recovery_cohort"):
            for value in (1, -1, "-1", None, "invalid", "0.5", 0.5, 0.0,
                          False, True, [], {}):
                with self.subTest(subset=subset, value=value):
                    state = {"pending_priority_total": 0, "pending_priority_36h": 0,
                             "pending_recovery_cohort": 0, subset: value}
                    with self.assertRaises((TypeError, ValueError)):
                        review_gate.priority_pending_count(state)

    def test_legacy_fallbacks_are_preserved(self):
        cases = (
            ({"pending_priority_36h": 3, "pending_fresh_24h": 1}, 3),
            ({"pending_priority_36h": 0, "pending_fresh_24h": 1}, 0),
            ({"pending_fresh_24h": 2}, 2),
        )
        for state, expected in cases:
            with self.subTest(state=state):
                self.assertEqual(review_gate.priority_pending_count(state), expected)

    def test_malformed_total_never_falls_back_to_zero(self):
        for total in (None, "", "invalid", "1.5", -1, "-1", False, True,
                      0.5, 0.0, float("nan"), float("inf"), [], {}):
            with self.subTest(total=total):
                with self.assertRaises((TypeError, ValueError)):
                    review_gate.priority_pending_count(
                        recovery_state(pending_priority_total=total)
                    )


class RecoveryGateTests(unittest.TestCase):
    def run_gate(self, module, state, *, run_log=None):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            log_path = Path(directory) / "runs.json"
            output_path = Path(directory) / "output.txt"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            log_path.write_text(json.dumps(run_log or {"runs": []}), encoding="utf-8")
            with mock.patch.object(sys, "argv", [module.__name__ + ".py"]), \
                    mock.patch.object(review_gate, "REVIEW_STATE_PATH", state_path), \
                    mock.patch.object(review_gate, "RUN_LOG_PATH", log_path), \
                    mock.patch.object(module, "datetime", FixedDateTime), \
                    mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_path)}), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = module.main()
            return code, output_path.read_text(encoding="utf-8")

    def test_retained_only_queue_runs_fresh_and_blocks_history(self):
        self.assertEqual(self.run_gate(fresh_review_gate, recovery_state()),
                         (0, "should_run=true\n"))
        self.assertEqual(self.run_gate(history_review_gate, recovery_state()),
                         (0, "should_run=false\n"))

    def test_completed_protected_queue_releases_history(self):
        state = recovery_state(pending_priority_total=0, pending_recovery_cohort=0)
        self.assertEqual(self.run_gate(fresh_review_gate, state),
                         (0, "should_run=false\n"))
        self.assertEqual(self.run_gate(history_review_gate, state),
                         (0, "should_run=true\n"))

    def test_retention_does_not_bypass_existing_api_cap_or_interval(self):
        scenarios = (
            (recovery_state(), {"runs": [{"run_at": "2026-10-11T11:00:00Z",
                                         "summary_requests": 146}]}),
            (recovery_state(updated_at="2026-10-11T11:59:00Z"), {"runs": []}),
            (recovery_state(rate_limited=True, rate_limited_at="2026-10-11T11:59:00Z",
                            api_error_kind="unknown_rate_limit"), {"runs": []}),
        )
        for state, run_log in scenarios:
            with self.subTest(state=state, run_log=run_log):
                self.assertEqual(self.run_gate(fresh_review_gate, state, run_log=run_log),
                                 (0, "should_run=false\n"))

    def test_invalid_total_cannot_release_either_gate(self):
        for module in (fresh_review_gate, history_review_gate):
            for total in ("invalid", -1, 0.5, False):
                with self.subTest(gate=module.__name__, total=total), \
                        mock.patch.object(review_gate, "evaluate_review_gate") as evaluate:
                    with self.assertRaises(ValueError):
                        self.run_gate(module, recovery_state(pending_priority_total=total))
                    evaluate.assert_not_called()


class RecoveryCompletionTests(unittest.TestCase):
    def test_retained_only_checkpoint_is_not_marked_complete(self):
        with mock.patch.object(collect, "load_review_state", return_value=recovery_state()), \
                mock.patch.object(collect, "save_review_state") as save:
            review_fresh.refresh_completed_checkpoint()
        save.assert_not_called()

    def test_invalid_total_cannot_save_completed_checkpoint(self):
        for total in (-1, "invalid", 0.5):
            with self.subTest(total=total), \
                    mock.patch.object(collect, "load_review_state", return_value=recovery_state(
                        pending_priority_total=total)), \
                    mock.patch.object(collect, "save_review_state") as save:
                with self.assertRaises(ValueError):
                    review_fresh.refresh_completed_checkpoint()
                save.assert_not_called()

    def test_empty_total_can_refresh_completion_without_fabricated_api_success(self):
        state = recovery_state(pending_priority_total=0, pending_recovery_cohort=0)
        with mock.patch.object(collect, "load_review_state", return_value=state), \
                mock.patch.object(collect, "now_utc", return_value=NOW), \
                mock.patch.object(collect, "load_public_payload", return_value={"items": []}), \
                mock.patch.object(collect, "save_review_state") as save:
            review_fresh.refresh_completed_checkpoint()
        saved = save.call_args.args[0]
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["backlog_status"], "in_progress")
        self.assertEqual(saved["model_requests"], 0)
        self.assertEqual(saved["reviewed"], 0)

    def test_delivery_assertion_uses_effective_total_and_rejects_invalid_total(self):
        for total, expected in ((16, 1), (0, 0), (-1, 1), ("invalid", 1), (0.5, 1)):
            with self.subTest(total=total), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.json"
                path.write_text(json.dumps(recovery_state(
                    pending_priority_total=total,
                    pending_recovery_cohort=0 if total == 0 else 16)),
                                encoding="utf-8")
                with mock.patch.object(assert_fresh_review_complete, "STATE_PATH", path), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(assert_fresh_review_complete.main(), expected)


class RecoveryReportTests(unittest.TestCase):
    def test_retained_only_report_remains_incomplete(self):
        state = recovery_state()
        self.assertEqual(report_review_outcome.review_outcome(
            state, scope="fresh", attempted=False), ("waiting", 16, 0))
        self.assertEqual(report_review_outcome.review_outcome(
            state, scope="fresh", attempted=True), ("in_progress", 16, 0))

    def test_zero_total_can_report_completion(self):
        self.assertEqual(report_review_outcome.review_outcome(
            recovery_state(pending_priority_total=0, pending_recovery_cohort=0),
            scope="fresh", attempted=False), ("completed", 0, 0))

    def test_report_displays_true_freshness_and_retained_counts_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            summary = Path(directory) / "summary.txt"
            path.write_text(json.dumps(recovery_state()), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.object(sys, "argv", ["report_review_outcome.py", "--scope", "fresh",
                                                 "--attempted", "false", "--state", str(path)]), \
                    mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), \
                    contextlib.redirect_stdout(stdout):
                self.assertEqual(report_review_outcome.main(), 0)
            text = stdout.getvalue()
            self.assertIn("Review outcome: waiting\n", text)
            self.assertIn("Pending fresh candidates: 16\n", text)
            self.assertIn("Pending within 36 hours: 0\n", text)
            self.assertIn("Pending retained recovery cohort: 16\n", text)
            self.assertNotIn("Review outcome: completed", text)
            self.assertEqual(text.strip(), summary.read_text(encoding="utf-8").strip())

    def test_inconsistent_zero_total_cannot_report_completion(self):
        state = recovery_state(pending_priority_total=0, pending_recovery_cohort=1)
        with self.assertRaises(ValueError):
            report_review_outcome.review_outcome(state, scope="fresh", attempted=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.object(sys, "argv", ["report_review_outcome.py", "--scope", "fresh",
                                                 "--attempted", "false", "--state", str(path)]), \
                    contextlib.redirect_stdout(stdout):
                self.assertEqual(report_review_outcome.main(), 1)
            self.assertIn("Review outcome unavailable", stdout.getvalue())
            self.assertNotIn("Review outcome: completed", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
