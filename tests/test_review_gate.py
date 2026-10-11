import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "review_gate.py"
SPEC = importlib.util.spec_from_file_location("review_gate", MODULE_PATH)
review_gate = importlib.util.module_from_spec(SPEC)
sys.modules["review_gate"] = review_gate
assert SPEC.loader is not None
SPEC.loader.exec_module(review_gate)


class RetryHintTests(unittest.TestCase):
    EVENT = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)

    def test_openai_reset_durations(self):
        for value, seconds in (
            ("120ms", 0.12),
            ("1s", 1),
            ("6m0s", 360),
            ("1h2m3.5s", 3723.5),
            ("1d2h3m4s5ms", 93784.005),
            (" 1.5s ", 1.5),
            (".5s", 0.5),
            ("0s", 0),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    review_gate.parse_retry_at(self.EVENT, "", value),
                    self.EVENT + timedelta(seconds=seconds),
                )

    def test_numeric_epoch_and_http_dates_remain_supported(self):
        expected = self.EVENT + timedelta(minutes=5)
        for value in (
            str(expected.timestamp()),
            "Tue, 28 Jul 2026 12:05:00 GMT",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    review_gate.parse_retry_at(self.EVENT, "", value),
                    expected,
                )
        for value in ("300", "Tue, 28 Jul 2026 12:05:00 GMT"):
            with self.subTest(retry_after=value):
                self.assertEqual(
                    review_gate.parse_retry_at(self.EVENT, value), expected
                )
        self.assertEqual(review_gate.parse_retry_at(self.EVENT, 0), self.EVENT)
        self.assertEqual(
            review_gate.parse_retry_at(self.EVENT, "", 0),
            datetime(1970, 1, 1, tzinfo=timezone.utc),
        )

    def test_latest_request_token_or_retry_hint_wins(self):
        for retry_after, reset, token_reset in (
            ("600", "6m0s", "120ms"),
            ("1", "10m", "6m0s"),
            ("1", "120ms", "10m"),
        ):
            with self.subTest(hints=(retry_after, reset, token_reset)):
                self.assertEqual(
                    review_gate.parse_retry_at(
                        self.EVENT, retry_after, reset, token_reset
                    ),
                    self.EVENT + timedelta(minutes=10),
                )

    def test_invalid_and_nonfinite_hints_fail_safely(self):
        for value in (
            "", "not-a-date", "NaN", "inf", "-inf", "1e1000", "1e100",
            "-1s", "NaNs", "1mgarbage", "1s-2s", "1 s", "1s!",
            "1s2s", "1s2m",
            "9" * 400 + "s", "Tue, 28 Jul 99999 12:05:00 GMT", None,
        ):
            with self.subTest(value=value):
                self.assertIsNone(
                    review_gate.parse_retry_at(self.EVENT, value)
                )
                self.assertIsNone(
                    review_gate.parse_retry_at(self.EVENT, "", value)
                )
                self.assertIsNone(
                    review_gate.parse_retry_at(self.EVENT, "", "", value)
                )
        self.assertIsNone(review_gate.parse_retry_at(self.EVENT, "-1"))

    def test_invalid_hint_does_not_hide_another_valid_hint(self):
        self.assertEqual(
            review_gate.parse_retry_at(self.EVENT, "inf", "NaN", "6m0s"),
            self.EVENT + timedelta(minutes=6),
        )


class ReviewGateTests(unittest.TestCase):
    NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)

    def state(self, **overrides):
        return {
            "updated_at": "2026-07-28T11:00:00Z",
            "pending_in_window": 100,
            **overrides,
        }

    def test_priority_pending_count_prefers_36h_and_falls_back_to_legacy(self):
        self.assertEqual(
            review_gate.priority_pending_count(
                {
                    "pending_priority_36h": 4,
                    "pending_fresh_24h": 0,
                }
            ),
            4,
        )
        self.assertEqual(
            review_gate.priority_pending_count(
                {
                    "pending_priority_36h": 0,
                    "pending_fresh_24h": 4,
                }
            ),
            0,
        )
        self.assertEqual(
            review_gate.priority_pending_count(
                {"pending_fresh_24h": 3}
            ),
            3,
        )
        with self.assertRaises(KeyError):
            review_gate.priority_pending_count({})

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

    def test_unknown_rate_limit_uses_conservative_cooldown(self):
        state = {
            "updated_at": "2026-07-28T11:00:00Z",
            "pending_in_window": 100,
            "rate_limited": True,
            "api_error_kind": "unknown_rate_limit",
            "rate_limited_at": "2026-07-28T11:30:00Z",
            "retry_after": "",
            "rate_limit_reset": "",
        }
        blocked = review_gate.evaluate_review_gate(
            {"runs": []},
            state,
            now=self.NOW,
            force=True,
        )

        self.assertFalse(blocked["cooldown_complete"])
        self.assertEqual(
            blocked["retry_blocked_until"],
            "2026-07-28T13:31:00+00:00",
        )
        self.assertFalse(blocked["should_run"])

        recovered = review_gate.evaluate_review_gate(
            {"runs": []},
            state,
            now=datetime(2026, 7, 28, 13, 32, tzinfo=timezone.utc),
            force=True,
        )
        self.assertTrue(recovered["cooldown_complete"])
        self.assertTrue(recovered["should_run"])

    def test_invalid_rate_limit_hints_use_conservative_cooldown(self):
        result = review_gate.evaluate_review_gate(
            {"runs": []},
            self.state(
                rate_limited=True,
                rate_limited_at="2026-07-28T11:30:00Z",
                retry_after="NaN",
                rate_limit_reset="inf",
                rate_limit_reset_tokens="1e999",
            ),
            now=self.NOW,
            force=True,
        )
        self.assertEqual(
            result["retry_blocked_until"], "2026-07-28T13:31:00+00:00"
        )
        self.assertFalse(result["should_run"])

    def test_token_reset_blocks_current_state_and_historical_rate_limits(self):
        state_fields = {
            "rate_limited": True,
            "api_error_kind": "transient_rate_limit",
            "rate_limited_at": "2026-07-28T11:59:30Z",
            "retry_after": "1",
            "rate_limit_reset": "120ms",
            "rate_limit_reset_tokens": "6m0s",
        }
        historical = {
            "run_at": "2026-07-28T11:59:30Z",
            "summary_requests": 1,
            **{f"summary_{key}": value for key, value in state_fields.items()},
        }
        for runs, state in (
            ([], self.state(**state_fields)),
            ([historical], self.state()),
        ):
            with self.subTest(state=state):
                result = review_gate.evaluate_review_gate(
                    {"runs": runs}, state, now=self.NOW, force=True
                )
                self.assertTrue(result["api_available"])
                self.assertFalse(result["cooldown_complete"])
                self.assertEqual(
                    result["retry_blocked_until"],
                    "2026-07-28T12:06:30+00:00",
                )
                self.assertFalse(result["should_run"])

    def test_api_account_failures_require_action_even_after_cooldown_or_force(self):
        for kind in ("quota_blocked", "authentication_failed", "access_denied"):
            for force in (False, True):
                with self.subTest(kind=kind, force=force):
                    result = review_gate.evaluate_review_gate(
                        {"runs": []},
                        self.state(
                            api_error_kind=kind,
                            api_error_at="2026-07-20T11:00:00Z",
                            api_error_message="Private account details",
                            rate_limited=False,
                            retry_after="1",
                        ),
                        now=self.NOW,
                        force=force,
                    )
                    self.assertFalse(result["api_available"])
                    self.assertTrue(result["user_action_reason"])
                    self.assertIn("user action required", result["detail"])
                    self.assertNotIn("Private account details", str(result))
                    self.assertFalse(result["should_run"])

    def test_successful_current_state_supersedes_historical_account_failures(self):
        for kind in ("quota_blocked", "authentication_failed", "access_denied"):
            with self.subTest(kind=kind):
                result = review_gate.evaluate_review_gate(
                    {
                        "runs": [{
                            "run_at": "2026-07-28T09:00:00Z",
                            "summary_requests": 1,
                            "summary_api_error_kind": kind,
                            # A legacy flag must not turn an account failure
                            # into a long timed block after recovery.
                            "summary_rate_limited": True,
                            "summary_retry_after": "86400",
                        }]
                    },
                    self.state(api_error_kind="", rate_limited=False),
                    now=self.NOW,
                )
                self.assertTrue(result["api_available"])
                self.assertEqual(result["user_action_reason"], "")
                self.assertTrue(result["cooldown_complete"])
                self.assertTrue(result["should_run"])

    def test_authorized_remedy_acknowledgement_permits_account_retry_only(self):
        for kind in ("quota_blocked", "authentication_failed", "access_denied"):
            for error_fields in (
                {"api_error_at": "2026-07-28T10:00:00Z"},
                {},  # Legacy failures fall back to updated_at (11:00).
            ):
                with self.subTest(kind=kind, error_fields=error_fields):
                    state = self.state(
                        api_error_kind=kind,
                        api_remedy_acknowledged_at="2026-07-28T11:30:00Z",
                        **error_fields,
                    )
                    original_state = state.copy()
                    result = review_gate.evaluate_review_gate(
                        {"runs": []}, state, now=self.NOW
                    )
                    self.assertTrue(result["api_available"])
                    self.assertTrue(result["api_remedy_acknowledged"])
                    self.assertEqual(result["user_action_reason"], "")
                    self.assertTrue(result["should_run"])
                    self.assertEqual(state, original_state)

    def test_remedy_acknowledgement_does_not_reset_rolling_usage(self):
        result = review_gate.evaluate_review_gate(
            {"runs": [{
                "run_at": "2026-07-28T10:00:00Z",
                "summary_requests": 146,
            }]},
            self.state(
                api_error_kind="quota_blocked",
                api_error_at="2026-07-28T11:00:00Z",
                api_remedy_acknowledged_at="2026-07-28T11:30:00Z",
                model_requests=1,
            ),
            now=self.NOW,
            force=True,
        )
        self.assertTrue(result["api_available"])
        self.assertEqual(result["all_requests_used"], 146)
        self.assertEqual(result["window_start"], "2026-07-27T12:00:00+00:00")
        self.assertFalse(result["quota_available"])
        self.assertFalse(result["should_run"])

    def test_remedy_acknowledgement_is_not_success_and_preserves_timed_cooldowns(self):
        for runs, state_fields, deadline in (
            ([{
                "run_at": "2026-07-28T09:00:00Z",
                "summary_requests": 1,
                "summary_rate_limited": True,
                "summary_api_error_kind": "unknown_rate_limit",
                "summary_retry_after": "86400",
            }], {}, "2026-07-29T09:01:00+00:00"),
            ([], {
                "quota_window_reset_at": "2026-07-28T13:00:00Z",
            }, "2026-07-28T13:01:00+00:00"),
            ([], {
                "rate_limited": True,
                "rate_limited_at": "2026-07-28T11:00:00Z",
                "retry_after": "10800",
            }, "2026-07-28T14:01:00+00:00"),
        ):
            with self.subTest(state_fields=state_fields):
                result = review_gate.evaluate_review_gate(
                    {"runs": runs},
                    self.state(
                        api_error_kind="quota_blocked",
                        api_error_at="2026-07-28T11:00:00Z",
                        api_remedy_acknowledged_at="2026-07-28T11:30:00Z",
                        model_requests=1,
                        **state_fields,
                    ),
                    now=self.NOW,
                    force=True,
                )
                self.assertTrue(result["api_available"])
                self.assertTrue(result["api_remedy_acknowledged"])
                self.assertFalse(result["cooldown_complete"])
                self.assertEqual(result["retry_blocked_until"], deadline)
                self.assertFalse(result["should_run"])

    def test_invalid_future_stale_or_equal_remedy_acknowledgements_block(self):
        for error_at, remedy_at in (
            ("2026-07-28T11:00:00Z", ""),
            ("2026-07-28T11:00:00Z", None),
            ("2026-07-28T11:00:00Z", "invalid"),
            ("2026-07-28T11:00:00Z", "2026-07-28T12:00:01Z"),
            ("2026-07-28T11:30:00Z", "2026-07-28T11:30:00Z"),
            ("2026-07-28T11:45:00Z", "2026-07-28T11:30:00Z"),
            ("invalid", "2026-07-28T11:30:00Z"),
        ):
            with self.subTest(error_at=error_at, remedy_at=remedy_at):
                result = review_gate.evaluate_review_gate(
                    {"runs": []},
                    self.state(
                        api_error_kind="quota_blocked",
                        api_error_at=error_at,
                        api_remedy_acknowledged_at=remedy_at,
                    ),
                    now=self.NOW,
                    force=True,
                )
                self.assertFalse(result["api_available"])
                self.assertFalse(result["api_remedy_acknowledged"])
                self.assertTrue(result["user_action_reason"])
                self.assertFalse(result["should_run"])

    def test_remedy_acknowledgement_does_not_override_transient_rate_limits(self):
        result = review_gate.evaluate_review_gate(
            {"runs": []},
            self.state(
                api_error_kind="unknown_rate_limit",
                api_error_at="2026-07-28T11:00:00Z",
                api_remedy_acknowledged_at="2026-07-28T11:30:00Z",
                rate_limited=True,
                rate_limited_at="2026-07-28T11:00:00Z",
            ),
            now=self.NOW,
            force=True,
        )
        self.assertFalse(result["api_remedy_acknowledged"])
        self.assertFalse(result["cooldown_complete"])
        self.assertFalse(result["should_run"])

    def test_real_later_api_success_supersedes_old_cooldown_hints(self):
        result = review_gate.evaluate_review_gate(
            {"runs": [{
                "run_at": "2026-07-28T09:00:00Z",
                "summary_requests": 1,
                "summary_rate_limited": True,
                "summary_api_error_kind": "unknown_rate_limit",
                "summary_retry_after": "86400",
            }]},
            self.state(
                model_requests=1,
                api_error_kind="",
                rate_limited=False,
                quota_window_reset_at="2026-07-29T09:00:00Z",
            ),
            now=self.NOW,
        )
        self.assertTrue(result["cooldown_complete"])
        self.assertEqual(result["retry_blocked_until"], "")
        self.assertEqual(result["all_requests_used"], 1)
        self.assertTrue(result["should_run"])

    def test_zero_request_skips_or_failed_requests_do_not_clear_old_cooldowns(self):
        for overrides in (
            {},
            {"model_requests": 0},
            {"model_requests": "invalid"},
            {"model_requests": 1, "api_error_kind": "unknown_rate_limit"},
            {"model_requests": 1, "rate_limited": True},
        ):
            with self.subTest(overrides=overrides):
                result = review_gate.evaluate_review_gate(
                    {"runs": [{
                        "run_at": "2026-07-28T09:00:00Z",
                        "summary_requests": 1,
                        "summary_rate_limited": True,
                        "summary_retry_after": "86400",
                    }]},
                    self.state(**overrides),
                    now=self.NOW,
                    force=True,
                )
                self.assertFalse(result["cooldown_complete"])
                self.assertEqual(
                    result["retry_blocked_until"],
                    "2026-07-29T09:01:00+00:00",
                )
                self.assertFalse(result["should_run"])

    def test_success_only_supersedes_strictly_older_rate_limit_events(self):
        for event_at in ("2026-07-28T11:00:00Z", "2026-07-28T11:30:00Z"):
            with self.subTest(event_at=event_at):
                result = review_gate.evaluate_review_gate(
                    {"runs": [{
                        "run_at": event_at,
                        "summary_requests": 1,
                        "summary_rate_limited": True,
                        "summary_retry_after": "86400",
                    }]},
                    self.state(model_requests=1, rate_limited=False),
                    now=self.NOW,
                    force=True,
                )
                self.assertFalse(result["cooldown_complete"])
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

    def test_completed_long_cooldown_does_not_erase_rolling_usage(self):
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

        self.assertEqual(result["all_requests_used"], 147)
        self.assertEqual(
            result["window_start"],
            "2026-07-27T12:00:00+00:00",
        )
        self.assertTrue(result["cooldown_complete"])
        self.assertFalse(result["should_run"])

    def test_persisted_quota_reset_does_not_erase_rolling_usage(self):
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

        self.assertEqual(result["all_requests_used"], 147)
        self.assertEqual(
            result["window_start"],
            "2026-07-27T12:00:00+00:00",
        )
        self.assertTrue(result["cooldown_complete"])
        self.assertFalse(result["should_run"])

    def test_future_legacy_quota_reset_is_only_a_cooldown_hint(self):
        result = review_gate.evaluate_review_gate(
            {"runs": [{
                "run_at": "2026-07-28T10:00:00Z",
                "summary_requests": 3,
            }]},
            self.state(quota_window_reset_at="2026-07-28T13:00:00Z"),
            now=self.NOW,
            force=True,
        )
        self.assertFalse(result["cooldown_complete"])
        self.assertEqual(
            result["retry_blocked_until"], "2026-07-28T13:01:00+00:00"
        )
        self.assertEqual(result["all_requests_used"], 3)
        self.assertEqual(result["window_start"], "2026-07-27T12:00:00+00:00")
        self.assertFalse(result["should_run"])

    def test_daily_reservation_and_global_limit_remain_conservative(self):
        for used, allowed in ((119, True), (120, False)):
            with self.subTest(used=used):
                result = review_gate.evaluate_review_gate(
                    {"runs": [
                        {
                            "run_at": "2026-07-27T11:59:59Z",
                            "summary_requests": 100,
                        },
                        {
                            "run_at": "2026-07-27T12:00:00Z",
                            "summary_requests": used,
                        },
                    ]},
                    self.state(),
                    now=self.NOW,
                    force=True,
                )
                self.assertEqual(result["reserved_requests"], 25)
                self.assertEqual(result["all_requests_used"], used)
                self.assertEqual(result["quota_available"], allowed)
                self.assertEqual(result["should_run"], allowed)

    def test_current_fresh_queue_does_not_reserve_future_fresh_work_twice(self):
        result = review_gate.evaluate_review_gate(
            {"runs": [{
                "run_at": "2026-07-28T10:00:00Z",
                "summary_requests": 110,
                "note": "Daily update",
            }]},
            self.state(),
            now=self.NOW,
        )
        self.assertEqual(result["reserved_requests"], 25)
        self.assertTrue(result["quota_available"])
        self.assertTrue(result["should_run"])

    def test_usage_remains_metered_until_recorded_completion_ages_out(self):
        for completion in (
            {"summary_usage_recorded_at": "2026-07-27T12:00:00Z"},
            {"duration_seconds": 7200},
            {"duration_seconds": "7200.5"},
        ):
            with self.subTest(completion=completion):
                result = review_gate.evaluate_review_gate(
                    {"runs": [{
                        "run_at": "2026-07-27T10:00:00Z",
                        "summary_requests": 145,
                        "note": "Review backlog",
                        **completion,
                    }]},
                    self.state(),
                    now=self.NOW,
                    force=True,
                )
                self.assertTrue(result["data_valid"])
                self.assertEqual(result["all_requests_used"], 145)
                self.assertEqual(result["backlog_requests_used"], 145)
                self.assertFalse(result["quota_available"])
                self.assertFalse(result["should_run"])

    def test_explicit_usage_completion_takes_precedence_over_legacy_duration(self):
        result = review_gate.evaluate_review_gate(
            {"runs": [{
                "run_at": "2026-07-27T10:00:00Z",
                "duration_seconds": 1,
                "summary_usage_recorded_at": "2026-07-27T13:00:00Z",
                "summary_requests": 120,
            }]},
            self.state(),
            now=self.NOW,
        )
        self.assertTrue(result["data_valid"])
        self.assertEqual(result["all_requests_used"], 120)
        self.assertFalse(result["should_run"])

    def test_completed_usage_expires_after_full_24_hours(self):
        for completion in (
            {"summary_usage_recorded_at": "2026-07-27T11:59:59Z"},
            {"duration_seconds": 7199},
            {},  # A legacy run without duration retains its original timestamp.
        ):
            with self.subTest(completion=completion):
                result = review_gate.evaluate_review_gate(
                    {"runs": [{
                        "run_at": "2026-07-27T10:00:00Z",
                        "summary_requests": 145,
                        **completion,
                    }]},
                    self.state(),
                    now=self.NOW,
                )
                self.assertTrue(result["data_valid"])
                self.assertEqual(result["all_requests_used"], 0)
                self.assertTrue(result["should_run"])

    def test_invalid_supplied_usage_timestamps_and_durations_fail_closed(self):
        invalid_fields = [
            {"summary_usage_recorded_at": value}
            for value in (
                "", None, "not-a-date", "2026-07-27T09:59:59Z",
                "2026-07-28T12:06:00Z",
            )
        ]
        invalid_fields.extend(
            {"duration_seconds": value}
            for value in (
                -1, "-0.1", None, "", "bad", True, "NaN", "inf", "-inf",
                1e100, float("nan"), float("inf"),
            )
        )
        invalid_fields.append({
            "summary_usage_recorded_at": "2026-07-27T13:00:00Z",
            "duration_seconds": "NaN",
        })
        for fields in invalid_fields:
            with self.subTest(fields=fields):
                result = review_gate.evaluate_review_gate(
                    {"runs": [{
                        "run_at": "2026-07-27T10:00:00Z",
                        "summary_requests": 1,
                        **fields,
                    }]},
                    self.state(),
                    now=self.NOW,
                    force=True,
                )
                self.assertFalse(result["data_valid"])
                self.assertFalse(result["should_run"])

    def test_usage_completion_does_not_rewrite_rate_limit_event_time(self):
        result = review_gate.evaluate_review_gate(
            {"runs": [{
                "run_at": "2026-07-28T09:00:00Z",
                "summary_usage_recorded_at": "2026-07-28T11:00:00Z",
                "summary_requests": 1,
                "summary_rate_limited": True,
                "summary_retry_after": "3600",
            }]},
            self.state(),
            now=self.NOW,
        )
        self.assertTrue(result["cooldown_complete"])
        self.assertTrue(result["should_run"])

    def test_usage_completion_preserves_legacy_logging_chronology(self):
        result = review_gate.evaluate_review_gate(
            {"runs": [
                {
                    "run_at": "2026-07-28T09:00:00Z",
                    "duration_seconds": 7200,
                },
                {
                    "run_at": "2026-07-28T10:00:00Z",
                    "summary_usage_recorded_at": "2026-07-28T10:30:00Z",
                    "summary_requests": 1,
                },
            ]},
            self.state(),
            now=self.NOW,
        )
        self.assertTrue(result["data_valid"])
        self.assertEqual(result["legacy_unmetered"], 1)
        self.assertEqual(result["all_requests_used"], 1)

    def test_backlog_limit_remains_96_requests(self):
        for used, allowed in ((94, True), (95, False)):
            with self.subTest(used=used):
                result = review_gate.evaluate_review_gate(
                    {"runs": [{
                        "run_at": "2026-07-28T10:00:00Z",
                        "summary_requests": used,
                        "note": "Review backlog",
                    }]},
                    self.state(),
                    now=self.NOW,
                    global_request_budget=1000,  # Isolate the backlog ceiling.
                )
                self.assertEqual(result["quota_available"], allowed)

    def test_daily_reservation_is_kept_at_exact_scheduled_minute(self):
        now = datetime(2026, 7, 28, 19, 17, tzinfo=timezone.utc)
        result = review_gate.evaluate_review_gate(
            {"runs": []}, self.state(), now=now
        )
        self.assertEqual(result["reserved_requests"], 25)

    def test_weekly_reservation_uses_sunday_0200_utc(self):
        for now, expected in (
            (datetime(2026, 8, 1, 1, 59, 59, tzinfo=timezone.utc), 25),
            (datetime(2026, 8, 1, 2, tzinfo=timezone.utc), 26),
            (datetime(2026, 8, 2, 1, 59, 59, tzinfo=timezone.utc), 26),
            (datetime(2026, 8, 2, 2, tzinfo=timezone.utc), 25),
        ):
            with self.subTest(now=now):
                result = review_gate.evaluate_review_gate(
                    {"runs": []}, self.state(), now=now
                )
                self.assertEqual(result["reserved_requests"], expected)

    def test_next_scheduled_time_supports_daily_minutes_and_weekdays(self):
        self.assertEqual(
            review_gate.next_scheduled_time(self.NOW, hour=19, minute=17),
            datetime(2026, 7, 28, 19, 17, tzinfo=timezone.utc),
        )
        self.assertEqual(
            review_gate.next_scheduled_time(
                datetime(2026, 7, 28, 19, 17, tzinfo=timezone.utc),
                hour=19,
                minute=17,
            ),
            datetime(2026, 7, 29, 19, 17, tzinfo=timezone.utc),
        )
        self.assertEqual(
            review_gate.next_scheduled_time(self.NOW, hour=2, weekday=6),
            datetime(2026, 8, 2, 2, tzinfo=timezone.utc),
        )
        with self.assertRaises(ValueError):
            review_gate.next_scheduled_time(self.NOW, hour=2, weekday=7)

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

