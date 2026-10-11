"""Failed-output persistence keeps queue progress tied to the durable ledger."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import prepare_api_safety_checkpoint
import report_review_outcome
import review_gate


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def durable_state(**overrides):
    return {
        "schema_version": 1,
        "review_version": "tech-innovation-v6",
        "updated_at": "2026-10-11T11:00:00Z",
        "status": "in_progress",
        "backlog_status": "in_progress",
        "pending_before": 12,
        "pending_fresh_before": 0,
        "pending_priority_before": 6,
        "pending_in_window": 12,
        "pending_fresh_24h": 0,
        "pending_priority_36h": 0,
        "pending_priority_total": 6,
        "pending_recovery_cohort": 6,
        "pending_expired": 0,
        "priority_window_hours": 36,
        "public_items": 100,
        "selected": 6,
        "reviewed": 6,
        "excluded": 0,
        "model_requests": 1,
        **overrides,
    }


def uncommitted_completion(**overrides):
    return durable_state(**{
        "updated_at": "2026-10-11T12:00:00Z",
        "updated_at_jst": "2026-10-11T21:00:00+09:00",
        "status": "completed",
        "backlog_status": "in_progress",
        "pending_in_window": 6,
        "pending_priority_total": 0,
        "pending_recovery_cohort": 0,
        "public_items": 106,
        "selected": 6,
        "reviewed": 6,
        "excluded": 2,
        "model_requests": 2,
        "rate_limited": False,
        "api_error_kind": "",
        "api_error_code": "",
        "api_error_type": "",
        "api_error_message": "",
        "api_error_at": "",
        "retry_after": "",
        "rate_limit_remaining": "4998",
        "rate_limit_reset": "12ms",
        **overrides,
    })


def fresh_gate(state, *, runs=None, now=None, force=False):
    gate_state = dict(state)
    gate_state["pending_in_window"] = review_gate.priority_pending_count(state)
    return review_gate.evaluate_review_gate(
        {"runs": runs or []}, gate_state,
        now=now or NOW + timedelta(minutes=26),
        force=force,
        next_request_budget=2,
        global_request_budget=146,
    )


class SafetyQueuePersistenceTests(unittest.TestCase):
    def test_validation_failure_cannot_persist_final_cohort_completion(self):
        current = uncommitted_completion()
        durable = durable_state()
        saved = prepare_api_safety_checkpoint.prepare_state(current, durable)
        self.assertEqual(saved["pending_priority_total"], 6)
        self.assertEqual(saved["pending_recovery_cohort"], 6)
        self.assertEqual(saved["pending_in_window"], 12)
        self.assertEqual(saved["public_items"], 100)
        self.assertNotEqual(saved["status"], "completed")
        self.assertEqual(report_review_outcome.review_outcome(
            saved, scope="fresh", attempted=False), ("waiting", 6, 0))

    def test_restored_queue_retries_naturally_after_existing_interval(self):
        saved = prepare_api_safety_checkpoint.prepare_state(
            uncommitted_completion(), durable_state())
        self.assertTrue(fresh_gate(saved)["should_run"])
        self.assertFalse(fresh_gate(saved, now=NOW + timedelta(minutes=24))["should_run"])
        self.assertTrue(fresh_gate(saved, now=NOW, force=True)["should_run"])

    def test_failure_checkpoint_does_not_reset_or_bypass_rolling_cap(self):
        saved = prepare_api_safety_checkpoint.prepare_state(
            uncommitted_completion(), durable_state())
        result = fresh_gate(saved, force=True, runs=[{
            "run_at": "2026-10-11T11:55:00Z", "summary_requests": 146,
        }])
        self.assertFalse(result["should_run"])
        self.assertEqual(result["all_requests_used"], 146)

    def test_latest_api_accounting_errors_and_observation_time_are_preserved(self):
        current = uncommitted_completion(
            api_error_kind="quota_blocked", api_error_code="insufficient_quota",
            api_error_type="insufficient_quota", api_error_message="Quota unavailable",
            api_error_at="2026-10-11T12:00:10Z", api_request_id="request-test",
            rate_limit_remaining="0", rate_limit_reset_tokens="30s",
            retry_after="60", request_budget_reached=True,
            api_remedy_acknowledged_at="2026-10-11T10:00:00Z",
        )
        saved = prepare_api_safety_checkpoint.prepare_state(current, durable_state())
        for field in (
            "updated_at", "updated_at_jst", "model_requests", "api_error_kind",
            "api_error_code", "api_error_type", "api_error_message", "api_error_at",
            "api_request_id", "rate_limited", "rate_limit_remaining", "rate_limit_reset",
            "rate_limit_reset_tokens", "retry_after", "request_budget_reached",
            "api_remedy_acknowledged_at",
        ):
            with self.subTest(field=field):
                self.assertEqual(saved[field], current[field])
        for field in ("selected", "reviewed", "excluded"):
            self.assertEqual(saved[field], 0)
        self.assertFalse(fresh_gate(saved, force=True)["should_run"])
        self.assertEqual(report_review_outcome.review_outcome(
            saved, scope="fresh", attempted=False), ("blocked", 6, 0))

    def test_latest_rate_limit_still_blocks_recovery(self):
        current = uncommitted_completion(
            rate_limited=True, rate_limited_at="2026-10-11T12:00:00Z",
            api_error_kind="unknown_rate_limit", api_error_at="2026-10-11T12:00:00Z",
            rate_limit_reset="", rate_limit_remaining="",
        )
        saved = prepare_api_safety_checkpoint.prepare_state(current, durable_state())
        self.assertEqual(saved["pending_recovery_cohort"], 6)
        self.assertFalse(fresh_gate(saved, force=True)["should_run"])

    def test_every_pending_field_comes_only_from_durable_state(self):
        current = uncommitted_completion(pending_uncommitted_only=0)
        durable = durable_state(pending_durable_only=4)
        saved = prepare_api_safety_checkpoint.prepare_state(current, durable)
        self.assertEqual(
            {k: v for k, v in saved.items() if k.startswith("pending_")},
            {k: v for k, v in durable.items() if k.startswith("pending_")},
        )
        self.assertEqual(saved["priority_window_hours"], durable["priority_window_hours"])

    def test_missing_durable_queue_cannot_fall_back_to_uncommitted_zero(self):
        saved = prepare_api_safety_checkpoint.prepare_state(uncommitted_completion(), {})
        self.assertFalse(any(key.startswith("pending_") for key in saved))
        self.assertEqual(saved["model_requests"], 2)
        with self.assertRaises((KeyError, TypeError, ValueError)):
            review_gate.priority_pending_count(saved)
        with self.assertRaises((KeyError, TypeError, ValueError)):
            report_review_outcome.review_outcome(saved, scope="fresh", attempted=False)

    def test_malformed_durable_counts_never_report_false_completion(self):
        for count in (None, -1, "invalid", 0.5, False):
            with self.subTest(count=count):
                saved = prepare_api_safety_checkpoint.prepare_state(
                    uncommitted_completion(), durable_state(pending_priority_total=count))
                with self.assertRaises((KeyError, TypeError, ValueError)):
                    report_review_outcome.review_outcome(saved, scope="fresh", attempted=False)

    def test_inputs_are_not_mutated(self):
        current, durable = uncommitted_completion(), durable_state()
        expected_current, expected_durable = copy.deepcopy(current), copy.deepcopy(durable)
        prepare_api_safety_checkpoint.prepare_state(current, durable)
        self.assertEqual(current, expected_current)
        self.assertEqual(durable, expected_durable)

    def test_cli_reads_head_without_committing_master_or_cohort_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            state_path = data / "review_state.json"
            master_path = data / "master.csv"
            cohort_path = data / "review_cohort.json"
            state_path.write_text(json.dumps(durable_state()), encoding="utf-8")
            master_path.write_text("id,status\npinned,Pending\n", encoding="utf-8")
            cohort_path.write_text('{"active_ids": ["pinned"]}\n', encoding="utf-8")
            durable_master, durable_cohort = master_path.read_bytes(), cohort_path.read_bytes()

            def git(*args):
                return subprocess.run(
                    ["git", *args], cwd=root, check=True,
                    capture_output=True, text=True,
                ).stdout

            git("init", "--quiet")
            git("add", "data")
            git("-c", "user.name=Offline Test", "-c", "user.email=test@example.invalid",
                "commit", "--quiet", "-m", "Durable queue fixture")
            head = git("rev-parse", "HEAD")
            state_path.write_text(json.dumps(uncommitted_completion()), encoding="utf-8")
            master_path.write_text("id,status\npinned,Reviewed\n", encoding="utf-8")
            cohort_path.write_text('{"active_ids": []}\n', encoding="utf-8")
            uncommitted_master, uncommitted_cohort = master_path.read_bytes(), cohort_path.read_bytes()

            subprocess.run(
                [sys.executable, "-B", str(ROOT / "scripts/prepare_api_safety_checkpoint.py")],
                cwd=root, check=True, capture_output=True, text=True,
            )
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["pending_recovery_cohort"], 6)
            self.assertEqual(saved["pending_priority_total"], 6)
            self.assertEqual(saved["model_requests"], 2)
            self.assertEqual(saved["reviewed"], 0)
            self.assertEqual(git("rev-parse", "HEAD"), head)
            self.assertEqual(git("show", "HEAD:data/master.csv").encode(), durable_master)
            self.assertEqual(git("show", "HEAD:data/review_cohort.json").encode(), durable_cohort)
            self.assertEqual(master_path.read_bytes(), uncommitted_master)
            self.assertEqual(cohort_path.read_bytes(), uncommitted_cohort)

    def test_cli_missing_durable_state_retains_usage_but_removes_completion_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            path = root / "data/review_state.json"
            path.write_text(json.dumps(uncommitted_completion()), encoding="utf-8")
            subprocess.run(
                [sys.executable, "-B", str(ROOT / "scripts/prepare_api_safety_checkpoint.py")],
                cwd=root, check=True, capture_output=True, text=True,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["model_requests"], 2)
            self.assertFalse(any(key.startswith("pending_") for key in saved))
            with self.assertRaises((KeyError, TypeError, ValueError)):
                review_gate.priority_pending_count(saved)


if __name__ == "__main__":
    unittest.main()
