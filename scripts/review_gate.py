#!/usr/bin/env python3
"""Decide whether a scheduled backlog review may use the review API."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN_LOG_PATH = ROOT / "data" / "run_log.json"
REVIEW_STATE_PATH = ROOT / "data" / "review_state.json"


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


def parse_retry_at(
    event_at: datetime,
    retry_after: Any,
    reset_at: Any = "",
) -> datetime | None:
    candidates: list[datetime] = []
    reset_text = str(reset_at or "").strip()
    if reset_text:
        try:
            candidates.append(
                datetime.fromtimestamp(float(reset_text), tz=timezone.utc)
            )
        except (OverflowError, TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(reset_text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                candidates.append(parsed.astimezone(timezone.utc))
            except (TypeError, ValueError):
                pass

    retry_text = str(retry_after or "").strip()
    if retry_text:
        try:
            candidates.append(
                event_at + timedelta(seconds=max(0.0, float(retry_text)))
            )
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(retry_text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                candidates.append(parsed.astimezone(timezone.utc))
            except (TypeError, ValueError):
                pass
    return max(candidates, default=None)


def next_scheduled_time(
    now: datetime,
    *,
    hour: int,
    weekday: int | None = None,
) -> datetime:
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
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
) -> dict[str, Any]:
    now = now.astimezone(timezone.utc)
    details: list[str] = []
    checkpoint_due = True
    pending_available = True
    data_valid = True

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
        try:
            event_at = parse_datetime(
                run.get("summary_rate_limited_at") or run["run_at"]
            )
        except (KeyError, TypeError, ValueError):
            continue
        retry_at = parse_retry_at(
            event_at,
            run.get("summary_retry_after", ""),
            run.get("summary_rate_limit_reset", ""),
        )
        if retry_at is not None:
            rate_limit_events.append((event_at, retry_at))

    if review_state.get("rate_limited"):
        try:
            event_at = parse_datetime(
                review_state.get("rate_limited_at")
                or review_state["updated_at"]
            )
            retry_at = parse_retry_at(
                event_at,
                review_state.get("retry_after", ""),
                review_state.get("rate_limit_reset", ""),
            )
            if retry_at is not None:
                rate_limit_events.append((event_at, retry_at))
        except (KeyError, TypeError, ValueError):
            pass

    persisted_quota_reset: datetime | None = None
    try:
        if review_state.get("quota_window_reset_at"):
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
    completed_long_cooldowns = [
        retry_at
        for event_at, retry_at in rate_limit_events
        if retry_at <= now and retry_at - event_at >= timedelta(minutes=5)
    ]
    if (
        persisted_quota_reset is not None
        and now - timedelta(hours=24) <= persisted_quota_reset <= now
    ):
        completed_long_cooldowns.append(persisted_quota_reset)
    known_window_reset = max(completed_long_cooldowns, default=None)
    if known_window_reset is not None:
        window_start = max(window_start, known_window_reset)
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
        except (KeyError, TypeError, ValueError):
            data_valid = False
            continue
        if run_at > now + timedelta(minutes=5):
            data_valid = False
            continue
        if run_at < window_start:
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

    quota_window_end = (
        known_window_reset + timedelta(hours=24)
        if known_window_reset is not None
        else now + timedelta(hours=24)
    )
    reserved_requests = 0
    if next_scheduled_time(now, hour=21) < quota_window_end:
        reserved_requests += 25
    if next_scheduled_time(now, hour=20, weekday=5) < quota_window_end:
        reserved_requests += 25

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
        and cooldown_complete
        and quota_available
    )
    return {
        "should_run": should_run,
        "checkpoint_due": checkpoint_due,
        "pending_available": pending_available,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
