"""Quiet scheduled deferrals must not hide actual failures or claim completion."""

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


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fresh_review_gate
import history_review_gate
import report_review_outcome
import review_gate


class QuietReviewOutcomeTests(unittest.TestCase):
    def state(self, **overrides):
        return {
            "updated_at": "2026-10-11T01:00:00Z",
            "pending_priority_36h": 12,
            "pending_in_window": 24,
            "status": "in_progress",
            "backlog_status": "in_progress",
            "reviewed": 6,
            "model_requests": 2,
            **overrides,
        }

    def test_account_failures_remain_blocked_but_only_new_attempts_fail(self):
        for scope, count, status_field in (
            ("fresh", 12, "status"), ("backlog", 24, "backlog_status"),
        ):
            for kind in ("quota_blocked", "authentication_failed", "access_denied"):
                for fields in ({"api_error_kind": kind}, {status_field: kind}):
                    for attempted in (True, False):
                        with self.subTest(scope=scope, fields=fields, attempted=attempted):
                            self.assertEqual(
                                report_review_outcome.review_outcome(
                                    self.state(**fields), scope=scope, attempted=attempted,
                                ),
                                ("blocked", count, int(attempted)),
                            )

    def test_persisted_rate_and_generic_failures_wait_without_new_attempt(self):
        for scope, count, status_field in (
            ("fresh", 12, "status"), ("backlog", 24, "backlog_status"),
        ):
            for fields in (
                {"rate_limited": True},
                {"api_error_kind": "rate_limited"},
                {"api_error_kind": "unknown_rate_limit"},
                {"api_error_kind": "api_error"},
                {status_field: "rate_limited"},
                {status_field: "review_failed"},
            ):
                for attempted, outcome, code in (
                    (False, "waiting", 0), (True, "blocked", 1),
                ):
                    with self.subTest(scope=scope, fields=fields, attempted=attempted):
                        self.assertEqual(
                            report_review_outcome.review_outcome(
                                self.state(**fields), scope=scope, attempted=attempted,
                            ),
                            (outcome, count, code),
                        )

    def test_local_budget_or_age_deferral_does_not_reuse_old_batch_progress(self):
        for scope, count in (("fresh", 12), ("backlog", 24)):
            for reviewed in (0, 6):
                for fields in ({}, {"request_budget_reached": True}):
                    with self.subTest(scope=scope, reviewed=reviewed, fields=fields):
                        state = self.state(reviewed=reviewed, **fields)
                        self.assertEqual(
                            report_review_outcome.review_outcome(
                                state, scope=scope, attempted=False,
                            ),
                            ("waiting", count, 0),
                        )
                        self.assertEqual(
                            report_review_outcome.review_outcome(
                                state, scope=scope, attempted=True,
                            ),
                            ("in_progress", count, 0) if reviewed else ("no_progress", count, 1),
                        )

    def test_zero_pending_is_complete_for_its_scope_even_with_old_failure(self):
        for scope, pending_field in (
            ("fresh", "pending_priority_36h"), ("backlog", "pending_in_window"),
        ):
            for kind in ("", "quota_blocked", "authentication_failed", "access_denied",
                         "rate_limited", "unknown_rate_limit", "api_error"):
                for attempted in (True, False):
                    with self.subTest(scope=scope, kind=kind, attempted=attempted):
                        self.assertEqual(
                            report_review_outcome.review_outcome(
                                self.state(**{pending_field: 0}, reviewed=0,
                                           api_error_kind=kind, rate_limited=bool(kind)),
                                scope=scope, attempted=attempted,
                            ),
                            ("completed", 0, 0),
                        )

    def run_report(self, state, *, scope, attempted):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            summary_path = Path(directory) / "summary.txt"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.object(sys, "argv", [
                "report_review_outcome.py", "--scope", scope,
                "--attempted", str(attempted).lower(), "--state", str(state_path),
            ]), mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}), \
                    contextlib.redirect_stdout(stdout):
                code = report_review_outcome.main()
            return code, stdout.getvalue(), summary_path.read_text(encoding="utf-8")

    def test_pending_summaries_explicitly_remain_incomplete(self):
        cases = (
            (False, {}, "waiting", 0),
            (False, {"rate_limited": True}, "waiting", 0),
            (False, {"api_error_kind": "api_error"}, "waiting", 0),
            (False, {"request_budget_reached": True}, "waiting", 0),
            (False, {"api_error_kind": "quota_blocked"}, "blocked", 0),
            (False, {"api_error_kind": "authentication_failed"}, "blocked", 0),
            (False, {"api_error_kind": "access_denied"}, "blocked", 0),
            (True, {"rate_limited": True}, "blocked", 1),
            (True, {"api_error_kind": "quota_blocked"}, "blocked", 1),
            (True, {"api_error_kind": "api_error"}, "blocked", 1),
            (True, {"reviewed": 0}, "no_progress", 1),
            (True, {}, "in_progress", 0),
        )
        for scope, count in (("fresh", 12), ("backlog", 24)):
            for attempted, fields, outcome, expected_code in cases:
                with self.subTest(scope=scope, attempted=attempted, fields=fields):
                    code, stdout, summary = self.run_report(
                        self.state(**fields), scope=scope, attempted=attempted,
                    )
                    self.assertEqual(code, expected_code)
                    self.assertEqual(summary.strip(), stdout.strip())
                    self.assertIn(f"Review outcome: {outcome}\n", summary)
                    self.assertIn(f"Pending {scope} candidates: {count}\n", summary)
                    self.assertIn("The queue is incomplete", summary)
                    self.assertIn("not delivery readiness", summary)
                    self.assertNotIn("Review outcome: completed", summary)
                    if fields.get("api_error_kind") in (
                        "quota_blocked", "authentication_failed", "access_denied",
                    ):
                        self.assertIn("Account/configuration action is required", summary)
                    if attempted:
                        self.assertIn("Reviewed in saved batch:", summary)
                        self.assertIn("API calls in saved batch:", summary)
                    else:
                        self.assertIn("No new API attempt was made", summary)
                        self.assertIn("does not indicate recovery", summary)
                        self.assertNotIn("Reviewed in saved batch:", summary)
                        self.assertNotIn("API calls in saved batch:", summary)


class QuietReviewGateCLITests(unittest.TestCase):
    NOW = datetime(2026, 10, 11, 2, 0, tzinfo=timezone.utc)
    MODULES = (review_gate, fresh_review_gate, history_review_gate)

    def state(self, **overrides):
        return {
            "updated_at": "2026-10-11T01:00:00Z",
            "pending_priority_36h": 12,
            "pending_in_window": 24,
            **overrides,
        }

    @contextlib.contextmanager
    def gate_cli(self, module, *, state=None, run_log=None,
                 invalid_file=None, invalid_content=None):
        """Use actual gate evaluation and file loading, with a deterministic clock."""
        now = self.NOW

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now if tz else now.replace(tzinfo=None)

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            log_path = Path(directory) / "run_log.json"
            output_path = Path(directory) / "github_output.txt"
            state_path.write_text(json.dumps(self.state() if state is None else state), encoding="utf-8")
            log_path.write_text(json.dumps({"runs": []} if run_log is None else run_log), encoding="utf-8")
            if invalid_file:
                path = state_path if invalid_file == "state" else log_path
                path.unlink()
                if invalid_content == "directory":
                    path.mkdir()
                elif invalid_content is not None:
                    path.write_text(invalid_content, encoding="utf-8")
            with mock.patch.object(sys, "argv", [module.__name__ + ".py"]), \
                    mock.patch.object(review_gate, "REVIEW_STATE_PATH", state_path), \
                    mock.patch.object(review_gate, "RUN_LOG_PATH", log_path), \
                    mock.patch.object(module, "datetime", FixedDateTime), \
                    mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_path)}), \
                    contextlib.redirect_stdout(io.StringIO()):
                yield output_path

    def assert_gate(self, module, expected_code, should_run, **inputs):
        with self.gate_cli(module, **inputs) as output:
            self.assertEqual(module.main(), expected_code)
            self.assertEqual(output.read_text(encoding="utf-8"),
                             f"should_run={str(should_run).lower()}\n")

    def test_valid_deferrals_exit_zero_without_releasing_the_review_batch(self):
        scenarios = (
            (self.state(updated_at="2026-10-11T01:59:00Z"), {"runs": []}),
            (self.state(rate_limited=True, rate_limited_at="2026-10-11T01:59:00Z",
                        api_error_kind="unknown_rate_limit"), {"runs": []}),
            (self.state(), {"runs": [{"run_at": "2026-10-11T01:00:00Z",
                                      "summary_requests": 146}]}),
            *((self.state(api_error_kind=kind), {"runs": []}) for kind in (
                "quota_blocked", "authentication_failed", "access_denied",
            )),
        )
        for module in self.MODULES:
            for state, run_log in scenarios:
                if module is history_review_gate:
                    # Exercise the safety deferral itself, rather than merely
                    # deferring because the protected fresh queue is pending.
                    state = {**state, "pending_priority_36h": 0}
                with self.subTest(gate=module.__name__, state=state, run_log=run_log):
                    self.assert_gate(module, 0, False, state=state, run_log=run_log)

    def test_zero_pending_and_fresh_queue_priority_are_quiet_deferrals(self):
        for module in self.MODULES:
            with self.subTest(gate=module.__name__):
                self.assert_gate(module, 0, False, state=self.state(
                    pending_priority_36h=0, pending_in_window=0,
                ))
        self.assert_gate(history_review_gate, 0, False, state=self.state())

    def test_valid_ready_queues_still_run(self):
        for module in self.MODULES:
            state = self.state()
            if module is history_review_gate:
                state["pending_priority_36h"] = 0
            with self.subTest(gate=module.__name__):
                self.assert_gate(module, 0, True, state=state)

    def test_invalid_safety_history_exits_nonzero_and_emits_false(self):
        invalid_logs = (
            {}, {"runs": "invalid"}, {"runs": [None]},
            {"runs": [{"run_at": "2026-10-11T01:00:00Z", "summary_requests": "invalid"}]},
            {"runs": [{"run_at": "2026-10-11T01:00:00Z", "summary_requests": -1}]},
            {"runs": [{"run_at": "invalid", "summary_requests": 1}]},
            {"runs": [{"run_at": "2026-10-11T01:00:00Z", "summary_requests": 1,
                       "duration_seconds": "NaN"}]},
        )
        for module in self.MODULES:
            for run_log in invalid_logs:
                with self.subTest(gate=module.__name__, run_log=run_log):
                    self.assert_gate(module, 1, False, run_log=run_log)
            for state in (self.state(updated_at="invalid"),
                          self.state(quota_window_reset_at="invalid")):
                with self.subTest(gate=module.__name__, state=state):
                    self.assert_gate(module, 1, False, state=state)

    def test_missing_malformed_and_unreadable_run_logs_fail_closed(self):
        for module in self.MODULES:
            for content in (None, "{broken json", "[]", "directory"):
                with self.subTest(gate=module.__name__, content=content):
                    self.assert_gate(module, 1, False, invalid_file="log", invalid_content=content)

    def test_missing_or_unreadable_state_never_releases_a_batch(self):
        for module in self.MODULES:
            for content in (None, "{broken json", "[]", "directory"):
                with self.subTest(gate=module.__name__, content=content):
                    with self.gate_cli(module, invalid_file="state", invalid_content=content) as output:
                        if module is review_gate:
                            self.assertEqual(module.main(), 1)
                        else:
                            # Missing protected counts already terminate these
                            # CLI entry points nonzero before evaluation.
                            with self.assertRaises(KeyError):
                                module.main()
                        if output.exists():
                            self.assertNotIn("should_run=true", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
