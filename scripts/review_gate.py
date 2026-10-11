#!/usr/bin/env python3
"""Decide whether a scheduled backlog review may use the review API."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN_LOG_PATH = ROOT / "data" / "run_log.json"
REVIEW_STATE_PATH = ROOT / "data" / "review_state.json"
API_USER_ACTION_REASONS = {
    "quota_blocked": "Resolve the API account quota or billing limit before retrying.",
    "authentication_failed": "Correct the configured API credentials before retrying.",
    "access_denied": "Resolve API account, project, or model access before retrying.",
}
RESET_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?|\.\d+)(ms|[dhms])")
RESET_DURATION_SECONDS = {
    "d": 24 * 60 * 60,
    "h": 60 * 60,
    "m": 60,
    "s": 1,
    "ms": 0.001,
}
RESET_DURATION_ORDER = {unit: index for index, unit in enumerate(RESET_DURATION_SECONDS)}


def priority_pending_count(review_state: dict[str, Any]) -> int:
    """Read the protected queue count, falling back to legacy 24-hour state."""
    key = (
        "pending_priority_36h"
        if "pending_priority_36h" in review_state
        else "pending_fresh_24h"
    )
    count = int(review_state[key])
    if count < 0:
        raise ValueError(f"{key} cannot be negative")
    return count


def parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def request_usage_recorded_at(run: dict[str, Any]) -> datetime:
    """Keep a batch's usage in the rolling window until its completion ages out."""
    run_at = parse_datetime(run["run_at"])
    duration_value = run.get("duration_seconds", 0)
    if isinstance(duration_value, bool):
        raise ValueError("invalid run duration")
    duration_seconds = float(duration_value)
    if not math.isfinite(duration_seconds) or duration_seconds < 0:
        raise ValueError("invalid run duration")
    duration = timedelta(seconds=duration_seconds)
    usage_at = (
        parse_datetime(run["summary_usage_recorded_at"])
        if "summary_usage_recorded_at" in run
        else run_at + duration
    )
    if usage_at < run_at:
        raise ValueError("usage completion precedes the run start")
    return usage_at


def parse_retry_at(
    event_at: datetime,
    retry_after: Any,
    reset_at: Any = "",
    reset_tokens_at: Any = "",
) -> datetime | None:
    """Read relative OpenAI resets, legacy epochs, and Retry-After hints."""
    candidates: list[datetime] = []
    for reset_hint in (reset_at, reset_tokens_at):
        reset_text = str(reset_hint if reset_hint is not None else "").strip()
        if not reset_text:
            continue
        try:
            parts = list(RESET_DURATION_PART.finditer(reset_text))
            if parts and "".join(part.group() for part in parts) == reset_text:
                units = [RESET_DURATION_ORDER[part.group(2)] for part in parts]
                if units != sorted(set(units)):
                    raise ValueError("invalid reset duration units")
                seconds = sum(
                    float(part.group(1)) * RESET_DURATION_SECONDS[part.group(2)]
                    for part in parts
                )
                if not math.isfinite(seconds):
                    raise ValueError("nonfinite reset duration")
                candidates.append(event_at + timedelta(seconds=seconds))
            else:
                timestamp = float(reset_text)
                if not math.isfinite(timestamp):
                    raise ValueError("nonfinite reset timestamp")
                candidates.append(
                    datetime.fromtimestamp(timestamp, tz=timezone.utc)
                )
        except (OverflowError, OSError, TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(reset_text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                candidates.append(parsed.astimezone(timezone.utc))
            except (OverflowError, OSError, TypeError, ValueError):
                pass

    retry_text = str(retry_after if retry_after is not None else "").strip()
    if retry_text:
        try:
            seconds = float(retry_text)
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("invalid retry duration")
            candidates.append(event_at + timedelta(seconds=seconds))
        except (OverflowError, OSError, TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(retry_text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                candidates.append(parsed.astimezone(timezone.utc))
            except (OverflowError, OSError, TypeError, ValueError):
                pass
    return max(candidates, default=None)


def next_scheduled_time(
    now: datetime,
    *,
    hour: int,
    minute: int = 0,
    weekday: int | None = None,
) -> datetime:
    if weekday is not None and not 0 <= weekday <= 6:
        raise ValueError("weekday must be between 0 and 6")
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while weekday is not None and candidate.weekday() != weekday:
        candidate += timedelta(days=1)
    return candidate


def evaluate_review_gate(
    run_log_payload: dict[str, Any],
    review_state: dict[str, Any],
    *,
    now: datetime,
    force: bool = False,
    minimum_age_seconds: int = 25 * 60,
    backlog_request_budget: int = 96,
    global_request_budget: int = 146,
    next_request_budget: int = 2,
    cooldown_safety_seconds: int = 60,
    unknown_rate_limit_cooldown_seconds: int = 2 * 60 * 60,
) -> dict[str, Any]:
    now = now.astimezone(timezone.utc)
    details: list[str] = []
    checkpoint_due = True
    pending_available = True
    data_valid = True
    checkpoint: datetime | None = None
    # Historical run failures are not authoritative for persistent API blocks:
    # a later successful state must be able to supersede them.
    api_error_kind = str(review_state.get("api_error_kind") or "").strip()
    user_action_reason = API_USER_ACTION_REASONS.get(api_error_kind, "")
    api_available = not user_action_reason
    api_remedy_acknowledged = False
    if user_action_reason and "api_remedy_acknowledged_at" in review_state:
        try:
            remedy_at = parse_datetime(review_state["api_remedy_acknowledged_at"])
            error_at = parse_datetime(
                review_state.get("api_error_at") or review_state["updated_at"]
            )
            api_remedy_acknowledged = error_at < remedy_at <= now
        except (KeyError, OverflowError, TypeError, ValueError):
            pass
        if api_remedy_acknowledged:
            api_available = True
            user_action_reason = ""
            details.append("API account remedy acknowledged; retry may be attempted")
    # An authorized remedy permits retrying an account failure. It is not a
    # successful API observation and must not clear cooldowns or metered usage.
    if not api_available:
        details.append(
            f"API unavailable ({api_error_kind}); user action required: "
            f"{user_action_reason}"
        )

    try:
        checkpoint = parse_datetime(review_state["updated_at"])
        age_seconds = int((now - checkpoint).total_seconds())
        if age_seconds < -300:
            raise ValueError(
                f"checkpoint is unexpectedly in the future ({age_seconds}s)"
            )
        age_seconds = max(0, age_seconds)
        checkpoint_due = force or age_seconds >= minimum_age_seconds
        details.append(
            f"checkpoint age={age_seconds}s; minimum={minimum_age_seconds}s"
        )
    except (KeyError, TypeError, ValueError) as exc:
        checkpoint_due = False
        data_valid = False
        details.append(f"checkpoint unavailable; blocking review: {exc}")

    successful_api_observation_at: datetime | None = None
    if (
        data_valid
        and checkpoint is not None
        and checkpoint <= now
        and not api_error_kind
        and not review_state.get("rate_limited")
    ):
        try:
            if int(review_state.get("model_requests", 0)) > 0:
                successful_api_observation_at = checkpoint
        except (OverflowError, TypeError, ValueError):
            pass

    try:
        pending_available = int(review_state["pending_in_window"]) > 0
        details.append(
            f"pending_in_window={int(review_state['pending_in_window'])}"
        )
    except (KeyError, TypeError, ValueError):
        pending_available = False
        data_valid = False
        details.append("pending count unavailable; blocking review")

    runs = run_log_payload.get("runs", [])
    if "runs" not in run_log_payload or not isinstance(runs, list):
        runs = []
        data_valid = False
        details.append("run log unavailable or invalid; blocking review")

    rate_limit_events: list[tuple[datetime, datetime]] = []
    for run in runs:
        if not isinstance(run, dict) or not run.get("summary_rate_limited"):
            continue
        if str(run.get("summary_api_error_kind") or "") in API_USER_ACTION_REASONS:
            # Account failures require a remedy, not a timed retry. Only the
            # current state above decides whether that remedy is still needed.
            continue
        try:
            event_at = parse_datetime(
                run.get("summary_rate_limited_at") or run["run_at"]
            )
        except (KeyError, TypeError, ValueError):
            continue
        if (
            successful_api_observation_at is not None
            and event_at < successful_api_observation_at
        ):
            continue
        retry_at = parse_retry_at(
            event_at,
            run.get("summary_retry_after", ""),
            run.get("summary_rate_limit_reset", ""),
            run.get("summary_rate_limit_reset_tokens", ""),
        )
        if retry_at is None:
            retry_at = event_at + timedelta(
                seconds=max(60, unknown_rate_limit_cooldown_seconds)
            )
            details.append(
                "rate-limit response had no reset hint; "
                "applying conservative cooldown"
            )
        rate_limit_events.append((event_at, retry_at))

    if review_state.get("rate_limited") and api_available:
        try:
            event_at = parse_datetime(
                review_state.get("rate_limited_at")
                or review_state["updated_at"]
            )
            retry_at = parse_retry_at(
                event_at,
                review_state.get("retry_after", ""),
                review_state.get("rate_limit_reset", ""),
                review_state.get("rate_limit_reset_tokens", ""),
            )
            if retry_at is None:
                retry_at = event_at + timedelta(
                    seconds=max(60, unknown_rate_limit_cooldown_seconds)
                )
                details.append(
                    "rate-limit state had no reset hint; "
                    "applying conservative cooldown"
                )
            rate_limit_events.append((event_at, retry_at))
        except (KeyError, TypeError, ValueError):
            pass

    persisted_quota_reset: datetime | None = None
    try:
        if (
            review_state.get("quota_window_reset_at")
            and successful_api_observation_at is None
        ):
            persisted_quota_reset = parse_datetime(
                review_state["quota_window_reset_at"]
            )
    except (TypeError, ValueError):
        data_valid = False
        details.append("persisted quota reset is invalid; blocking review")

    retry_at = max(
        [
            retry_at
            for _, retry_at in rate_limit_events
            if retry_at + timedelta(seconds=cooldown_safety_seconds) > now
        ]
        + (
            [persisted_quota_reset]
            if (
                persisted_quota_reset is not None
                and persisted_quota_reset
                + timedelta(seconds=cooldown_safety_seconds)
                > now
            )
            else []
        ),
        default=None,
    )
    retry_blocked_until = (
        retry_at + timedelta(seconds=cooldown_safety_seconds)
        if retry_at is not None
        else None
    )
    cooldown_complete = retry_blocked_until is None
    if retry_blocked_until is not None:
        details.append(
            f"rate-limit cooldown until={retry_blocked_until.isoformat()}"
        )

    window_start = now - timedelta(hours=24)
    # Provider cooldowns govern when another request may be attempted. They
    # never erase usage from this application's rolling 24-hour safety budget.
    details.append(f"quota window starts={window_start.isoformat()}")

    all_requests_used = 0
    backlog_requests_used = 0
    legacy_unmetered = 0
    metered_run_times: list[datetime] = []
    for run in runs:
        if not isinstance(run, dict) or "summary_requests" not in run:
            continue
        try:
            metered_run_times.append(parse_datetime(run["run_at"]))
        except (KeyError, TypeError, ValueError):
            data_valid = False
    request_logging_started_at = min(metered_run_times, default=None)

    for run in runs:
        if not isinstance(run, dict):
            data_valid = False
            continue
        try:
            run_at = parse_datetime(run["run_at"])
            usage_at = request_usage_recorded_at(run)
        except (KeyError, OverflowError, TypeError, ValueError):
            data_valid = False
            continue
        if usage_at > now + timedelta(minutes=5):
            data_valid = False
            continue
        if usage_at < window_start:
            continue
        try:
            requests = int(run["summary_requests"])
            if requests < 0:
                raise ValueError("negative summary_requests")
        except KeyError:
            if (
                request_logging_started_at is not None
                and run_at < request_logging_started_at
            ):
                legacy_unmetered += 1
                continue
            data_valid = False
            continue
        except (TypeError, ValueError):
            data_valid = False
            continue
        all_requests_used += requests
        if run.get("note") == "Review backlog":
            backlog_requests_used += requests

    quota_window_end = now + timedelta(hours=24)
    reserved_requests = 0
    # Protect the next fixed collection stages (daily 25, weekly 1). Future
    # fresh review shares the remaining hard ceiling with this protected queue;
    # reserving its 85-request batch again would block today's same queue.
    if next_scheduled_time(now, hour=19, minute=17) <= quota_window_end:
        reserved_requests += 25
    if next_scheduled_time(now, hour=2, weekday=6) <= quota_window_end:
        reserved_requests += 1

    quota_available = (
        all_requests_used + reserved_requests + next_request_budget
        <= global_request_budget
        and backlog_requests_used + next_request_budget
        <= backlog_request_budget
    )
    details.append(
        f"model requests={all_requests_used}/{global_request_budget}; "
        f"reserved={reserved_requests}; "
        f"backlog requests={backlog_requests_used}/{backlog_request_budget}; "
        f"legacy unmetered runs={legacy_unmetered}"
    )

    should_run = (
        data_valid
        and checkpoint_due
        and pending_available
        and api_available
        and cooldown_complete
        and quota_available
    )
    return {
        "should_run": should_run,
        "checkpoint_due": checkpoint_due,
        "pending_available": pending_available,
        "api_available": api_available,
        "api_remedy_acknowledged": api_remedy_acknowledged,
        "user_action_reason": user_action_reason,
        "cooldown_complete": cooldown_complete,
        "quota_available": quota_available,
        "data_valid": data_valid,
        "all_requests_used": all_requests_used,
        "backlog_requests_used": backlog_requests_used,
        "reserved_requests": reserved_requests,
        "legacy_unmetered": legacy_unmetered,
        "window_start": window_start.isoformat(),
        "retry_blocked_until": (
            retry_blocked_until.isoformat()
            if retry_blocked_until is not None
            else ""
        ),
        "detail": "; ".join(details),
    }


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass only the checkpoint-age test; quota safeguards remain active.",
    )
    parser.add_argument(
        "--request-budget",
        type=int,
        default=2,
        help="Maximum review API requests the proposed review may use.",
    )
    parser.add_argument("--run-log", type=Path, default=RUN_LOG_PATH)
    parser.add_argument("--review-state", type=Path, default=REVIEW_STATE_PATH)
    args = parser.parse_args()
    if args.request_budget < 1:
        parser.error("--request-budget must be at least 1")

    result = evaluate_review_gate(
        load_json(args.run_log),
        load_json(args.review_state),
        now=datetime.now(timezone.utc),
        force=args.force,
        next_request_budget=args.request_budget,
    )
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(
                f"should_run={'true' if result['should_run'] else 'false'}\n"
            )
    print(
        f"Backlog review due={result['should_run']}; {result['detail']}"
    )
    # Invalid safety history is an actionable failure, not expected waiting.
    return 0 if result["data_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

