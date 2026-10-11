import importlib.util
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import collect
import report_review_outcome


class APIReviewFailureTests(unittest.TestCase):
    NOW = datetime(2026, 10, 11, 1, 2, tzinfo=timezone.utc)

    def setUp(self):
        self.safe_state = {"updated_at": "2026-10-11T00:00:00Z"}

    def persisted_inputs(self, log, state):
        def load(path):
            return {"runs": log} if path == collect.RUN_LOG_JSON else state
        import review_gate
        return mock.patch.object(review_gate, "load_json", side_effect=load)

    def response(self, status=429, code="rate_limit_exceeded", error_type="rate_limit_error"):
        response = collect.requests.Response()
        response.status_code = status
        response._content = json.dumps({"error": {
            "code": code, "type": error_type,
            "message": "Never publish sk-proj-private-example or Bearer secret or user prose",
        }}).encode()
        response.headers["x-request-id"] = "req-diagnostic-123"
        return response

    def test_classifies_quota_variants_without_claiming_temporary_rate_limit(self):
        for code in ("insufficient_quota", "credit_balance_exhausted",
                     "project_spend_limit_exceeded", "organization_spend_limit_exceeded",
                     "organization_usage_limit_exceeded", "usage_limit_exceeded",
                     "billing_hard_limit_reached"):
            with self.subTest(code=code):
                result = collect.api_error_observation(self.response(code=code, error_type="insufficient_quota"))
                self.assertEqual(result["api_error_kind"], "quota_blocked")
                self.assertEqual(result["api_error_code"], code)

    def test_empty_429_is_unknown_not_invented_billing_cause(self):
        response = self.response()
        response._content = b""
        self.assertEqual(collect.api_error_observation(response)["api_error_kind"], "unknown_rate_limit")

    def test_error_diagnostics_never_store_raw_error_message_or_secrets(self):
        for field in ("code", "type"):
            response = self.response()
            body = json.loads(response.content)
            body["error"][field] = "sk-proj-private-example"
            response._content = json.dumps(body).encode()
            observation = collect.api_error_observation(response)
            self.assertNotIn("sk-proj", json.dumps(observation))
            self.assertNotIn("Bearer", json.dumps(observation))
            self.assertNotIn("user prose", json.dumps(observation))
            self.assertEqual(observation["api_request_id"], "req-diagnostic-123")

    def test_quota_request_stops_once_and_does_not_set_rate_limit_clock(self):
        stats = {"requests": 0}
        response = self.response(code="credit_balance_exhausted", error_type="insufficient_quota")
        with mock.patch.object(collect.requests, "post", return_value=response) as post:
            with self.assertRaises(collect.SummaryAPIError):
                collect.japanese_summary_request(
                    [{"canonical_id": "a", "title": "AI research"}], "fake-test-key",
                    collect.DEFAULT_JAPANESE_SUMMARY_MODEL, stats,
                )
        self.assertEqual(post.call_count, 1)
        self.assertEqual(stats["requests"], 1)
        self.assertEqual(stats["api_error_kind"], "quota_blocked")
        self.assertNotIn("rate_limited_at", stats)

    def test_temporary_limit_preserves_request_and_token_reset_hints(self):
        stats = {"requests": 0}
        response = self.response()
        response.headers.update({"x-ratelimit-reset-requests": "120ms",
                                 "x-ratelimit-reset-tokens": "6m0s"})
        with mock.patch.object(collect.requests, "post", return_value=response):
            with self.assertRaises(collect.SummaryRateLimitError):
                collect.japanese_summary_request(
                    [{"canonical_id": "a", "title": "AI research"}], "fake-test-key",
                    collect.DEFAULT_JAPANESE_SUMMARY_MODEL, stats,
                )
        self.assertEqual(stats["api_error_kind"], "rate_limited")
        self.assertEqual(stats["reset_at"], "120ms")
        self.assertEqual(stats["rate_limit_reset_tokens"], "6m0s")

    def test_nonretryable_auth_and_access_errors_stop_once(self):
        for http_status, expected in ((401, "authentication_failed"), (403, "access_denied"), (400, "api_error")):
            stats = {"requests": 0}
            with self.subTest(status=http_status), mock.patch.object(
                collect.requests, "post", return_value=self.response(http_status, "error", "invalid_request_error")
            ) as post:
                with self.assertRaises(collect.SummaryAPIError):
                    collect.japanese_summary_request(
                        [{"canonical_id": "a", "title": "AI research"}], "fake-test-key",
                        collect.DEFAULT_JAPANESE_SUMMARY_MODEL, stats,
                    )
                self.assertEqual(post.call_count, 1)
                self.assertEqual(stats["api_error_kind"], expected)

    def test_next_stage_skips_api_and_preserves_original_cooldown_timestamp(self):
        log = [{"run_at": "2026-10-11T01:00:00Z", "summary_requests": 1,
                "summary_rate_limited": True, "summary_rate_limited_at": "2026-10-11T01:01:00Z",
                "summary_api_error_kind": "unknown_rate_limit", "summary_retry_after": ""}]
        state = {"updated_at": "2026-10-10T23:00:00Z", "rate_limited": False}
        with self.persisted_inputs(log, state), \
             mock.patch.object(collect, "now_utc", return_value=self.NOW), \
             mock.patch.object(collect, "japanese_summary_request") as request, \
             mock.patch.dict(os.environ, {"OPENAI_API_KEY": "fake-test-key"}):
            result = collect.enrich_japanese_summaries(
                [{"canonical_id": "a", "title": "AI research", "scope_review_version": "old"}], [],
            )
        request.assert_not_called()
        self.assertTrue(result["api_call_skipped"])
        self.assertEqual(result["requests"], 0)
        self.assertEqual(result["rate_limited_at"], "2026-10-11T01:01:00Z")

    def test_account_block_survives_without_making_any_model_request(self):
        state = {"updated_at": "2026-10-10T01:00:00Z", "api_error_kind": "quota_blocked",
                 "api_error_code": "credit_balance_exhausted", "model_requests": 1}
        with self.persisted_inputs([], state), \
             mock.patch.object(collect, "now_utc", return_value=self.NOW), \
             mock.patch.object(collect, "japanese_summary_request") as request, \
             mock.patch.dict(os.environ, {"OPENAI_API_KEY": "fake-test-key"}):
            result = collect.enrich_japanese_summaries(
                [{"canonical_id": "a", "title": "AI research", "scope_review_version": "old"}], [],
            )
        request.assert_not_called()
        self.assertEqual(result["api_error_kind"], "quota_blocked")
        self.assertFalse(result["rate_limited"])
        self.assertEqual(result["requests"], 0)

    def test_quota_error_classification_survives_batch_and_runlog_fields(self):
        response = self.response(code="project_spend_limit_exceeded", error_type="insufficient_quota")
        with mock.patch.object(collect, "review_api_blocker", return_value={}), \
             mock.patch.object(collect.requests, "post", return_value=response), \
             mock.patch.dict(os.environ, {"OPENAI_API_KEY": "fake-test-key"}):
            result = collect.enrich_japanese_summaries(
                [{"canonical_id": "a", "title": "AI research", "scope_review_version": "old"}], [],
            )
        self.assertEqual(result["api_error_kind"], "quota_blocked")
        self.assertFalse(result["rate_limited"])
        self.assertEqual(collect.summary_api_fields(result, "summary_")["summary_api_error_code"], "project_spend_limit_exceeded")
        self.assertEqual(collect.review_state_statuses(1, 1, result), ("quota_blocked", "quota_blocked"))

    def test_successful_retry_clears_transient_error_diagnostics(self):
        failed = self.response(503, "server_is_overloaded", "service_unavailable_error")
        succeeded = self.response(200)
        succeeded._content = json.dumps({"output_text": '{"items":[{"id":"a","in_scope":false,"reason":"outside scope"}]}'}).encode()
        stats = {"requests": 0}
        with mock.patch.object(collect.requests, "post", side_effect=[failed, succeeded]), \
             mock.patch.object(collect.time, "sleep"):
            result = collect.japanese_summary_request(
                [{"canonical_id": "a", "title": "AI research"}], "fake-test-key",
                collect.DEFAULT_JAPANESE_SUMMARY_MODEL, stats,
            )
        self.assertIn("a", result)
        self.assertNotIn("api_error_kind", stats)

    def test_invalid_safety_counter_blocks_before_any_model_call(self):
        log = [{"run_at": "2026-10-11T01:00:00Z", "summary_requests": "invalid"}]
        with self.persisted_inputs(log, self.safe_state), \
             mock.patch.object(collect, "now_utc", return_value=self.NOW):
            guard = collect.review_api_blocker()
        self.assertTrue(guard["api_call_skipped"])
        self.assertEqual(guard["api_error_kind"], "api_error")

    def test_actual_rolling_cap_bounds_daily_and_manual_passes_without_reserving_twice(self):
        log = [{"run_at": "2026-10-11T01:00:00Z", "summary_requests": 145,
                "summary_rate_limited": False}]
        items = [{"canonical_id": str(i), "title": "AI research", "scope_review_version": "old"}
                 for i in range(3)]
        def fake_request(batch, token, model, stats):
            stats["requests"] += 1
            return {item["canonical_id"]: {"in_scope": False, "reason": "Outside scope"}
                    for item in batch}
        with self.persisted_inputs(log, self.safe_state), \
             mock.patch.object(collect, "now_utc", return_value=self.NOW), \
             mock.patch.object(collect, "japanese_summary_request", side_effect=fake_request) as request, \
             mock.patch.object(collect.time, "sleep"), \
             mock.patch.dict(os.environ, {"OPENAI_API_KEY": "fake-test-key", "JAPANESE_SUMMARY_BATCH_SIZE": "1",
                                         "JAPANESE_SUMMARY_REQUEST_BUDGET": "25"}):
            result = collect.enrich_japanese_summaries(items, items)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["requests"], 1)
        self.assertEqual(result["reviewed"], 1)
        self.assertTrue(result["request_budget_reached"])

    def test_absolute_cap_exhaustion_prevents_a_new_pass(self):
        log = [{"run_at": "2026-10-11T01:00:00Z", "summary_requests": 146}]
        with self.persisted_inputs(log, self.safe_state), \
             mock.patch.object(collect, "now_utc", return_value=self.NOW):
            guard = collect.review_api_blocker()
        self.assertTrue(guard["api_call_skipped"])
        self.assertTrue(guard["request_budget_reached"])

    def test_retry_loop_cannot_exceed_remaining_actual_cap(self):
        stats = {"requests": 0, "request_budget": 1}
        with mock.patch.object(collect.requests, "post", return_value=self.response(503)) as post, \
             mock.patch.object(collect.time, "sleep"):
            with self.assertRaises(collect.SummaryRequestBudgetError):
                collect.japanese_summary_request(
                    [{"canonical_id": "a", "title": "AI research"}], "fake-test-key",
                    collect.DEFAULT_JAPANESE_SUMMARY_MODEL, stats,
                )
        self.assertEqual(post.call_count, 1)


    def test_missing_safety_documents_fail_closed(self):
        for log, state in (({}, self.safe_state), ({"runs": []}, {}),
                           ({"runs": "invalid"}, self.safe_state)):
            with self.subTest(log=log, state=state):
                guard = collect.review_api_blocker(log, state)
                self.assertTrue(guard["api_call_skipped"])
                self.assertEqual(guard["api_error_kind"], "api_error")

    def test_zero_request_checkpoint_cannot_erase_last_real_account_failure(self):
        log = [{"run_at": "2026-10-11T01:00:00Z", "summary_requests": 1,
                "summary_api_error_kind": "quota_blocked", "summary_api_error_at": "2026-10-11T01:00:10Z"}]
        state = {"updated_at": "2026-10-11T01:01:00Z", "model_requests": 0, "api_error_kind": ""}
        with mock.patch.object(collect, "now_utc", return_value=self.NOW):
            guard = collect.review_api_blocker({"runs": log}, state)
        self.assertEqual(guard["api_error_kind"], "quota_blocked")
        self.assertTrue(guard["api_call_skipped"])

    def test_empty_selection_keeps_account_failure_for_later_queues(self):
        state = {"updated_at": "2026-10-11T01:00:00Z", "model_requests": 1,
                 "api_error_kind": "quota_blocked", "api_error_at": "2026-10-11T01:00:10Z"}
        with self.persisted_inputs([], state), \
             mock.patch.object(collect, "now_utc", return_value=self.NOW):
            result = collect.enrich_japanese_summaries([], [])
        self.assertEqual(result["requests"], 0)
        self.assertEqual(result["api_error_kind"], "quota_blocked")
        self.assertEqual(collect.review_state_statuses(1, 0, result), ("completed", "quota_blocked"))

    def test_explicit_remedy_acknowledgment_does_not_clear_usage_and_new_failure_reblocks(self):
        log = [{"run_at": "2026-10-11T01:00:00Z", "summary_requests": 1,
                "summary_api_error_kind": "quota_blocked", "summary_api_error_at": "2026-10-11T01:00:10Z"}]
        state = {"updated_at": "2026-10-11T01:00:00Z", "model_requests": 0,
                 "api_remedy_acknowledged_at": "2026-10-11T01:01:00Z"}
        with mock.patch.object(collect, "now_utc", return_value=self.NOW):
            self.assertEqual(collect.review_api_blocker({"runs": log}, state), {"request_budget_available": 145})
            log.append({"run_at": "2026-10-11T01:01:10Z", "summary_requests": 1,
                        "summary_api_error_kind": "quota_blocked", "summary_api_error_at": "2026-10-11T01:01:20Z"})
            self.assertEqual(collect.review_api_blocker({"runs": log}, state)["api_error_kind"], "quota_blocked")


class ReviewOutcomeTests(unittest.TestCase):
    def state(self, **extra):
        return {"pending_priority_36h": 12, "pending_in_window": 24,
                "status": "in_progress", "reviewed": 6, **extra}

    def test_rate_limited_attempt_fails_but_unchanged_cooldown_waits(self):
        for attempted, expected in ((True, ("blocked", 12, 1)),
                                    (False, ("waiting", 12, 0))):
            result = report_review_outcome.review_outcome(
                self.state(status="rate_limited", rate_limited=True, reviewed=0),
                scope="fresh", attempted=attempted,
            )
            self.assertEqual(result, expected)

    def test_progress_completion_and_skips_are_distinguished(self):
        self.assertEqual(report_review_outcome.review_outcome(self.state(), scope="fresh", attempted=True), ("in_progress", 12, 0))
        self.assertEqual(report_review_outcome.review_outcome(self.state(), scope="fresh", attempted=False), ("waiting", 12, 0))
        self.assertEqual(report_review_outcome.review_outcome(self.state(pending_priority_36h=0), scope="fresh", attempted=False), ("completed", 0, 0))
        self.assertEqual(report_review_outcome.review_outcome(self.state(reviewed=0), scope="fresh", attempted=True), ("no_progress", 12, 1))

    def test_outcome_step_runs_after_checkpoint_even_when_review_skipped(self):
        for workflow in ("review-backlog.yml", "review-history.yml"):
            content = (ROOT / ".github" / "workflows" / workflow).read_text()
            self.assertGreater(content.index("Report actual review outcome"), content.index("git push"))
            self.assertIn("if: always() && steps.review_due.outcome == 'success'", content)


if __name__ == "__main__":
    unittest.main()
