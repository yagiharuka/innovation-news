#!/usr/bin/env python3
"""Report saved review results after the checkpoint has been committed."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import review_gate


ROOT = Path(__file__).resolve().parents[1]
BLOCKED_STATUSES = {
    "rate_limited", "quota_blocked", "authentication_failed", "access_denied",
    "review_failed",
}
ACCOUNT_BLOCKS = {"quota_blocked", "authentication_failed", "access_denied"}


def review_outcome(state: dict, *, scope: str, attempted: bool) -> tuple[str, int, int]:
    pending = (review_gate.priority_pending_count(state) if scope == "fresh"
               else int(state["pending_in_window"]))
    if pending < 0:
        raise ValueError("Pending count cannot be negative")
    if pending == 0:
        return "completed", pending, 0
    status = state.get("status" if scope == "fresh" else "backlog_status", "")
    # An unchanged checkpoint is not a new failed API attempt. Keep the queue
    # visibly incomplete without sending a failure notification every time the
    # safety gate defers another scheduled batch.
    if not attempted:
        account_blocked = (
            status in ACCOUNT_BLOCKS or state.get("api_error_kind") in ACCOUNT_BLOCKS
        )
        return "blocked" if account_blocked else "waiting", pending, 0
    if (status in BLOCKED_STATUSES or state.get("rate_limited")
            or state.get("api_error_kind") in BLOCKED_STATUSES | {"api_error", "unknown_rate_limit"}):
        return "blocked", pending, 1
    if int(state.get("reviewed") or 0) <= 0:
        return "no_progress", pending, 1
    return "in_progress", pending, 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("fresh", "backlog"), default="fresh")
    parser.add_argument("--attempted", choices=("true", "false"), required=True)
    parser.add_argument("--state", type=Path, default=ROOT / "data" / "review_state.json")
    args = parser.parse_args()
    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
        outcome, pending, exit_code = review_outcome(
            state, scope=args.scope, attempted=args.attempted == "true",
        )
        queue_counts = [
            (label, int(state[key]))
            for key, label in (
                ("pending_priority_36h", "Pending within 36 hours"),
                ("pending_recovery_cohort", "Pending retained recovery cohort"),
            )
            if key in state
        ]
        if any(count < 0 for _, count in queue_counts):
            raise ValueError("Pending queue counts cannot be negative")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Review outcome unavailable: {type(exc).__name__}")
        return 1
    lines = [
        f"Review outcome: {outcome}", f"Pending {args.scope} candidates: {pending}",
        *(f"{label}: {count}" for label, count in queue_counts),
        f"Saved checkpoint: {state.get('updated_at', 'unknown')}",
        f"This job attempted review: {args.attempted}",
    ]
    if args.attempted == "true":
        lines.extend((f"Reviewed in saved batch: {state.get('reviewed', 0)}",
                      f"API calls in saved batch: {state.get('model_requests', 0)}"))
    if state.get("api_error_kind") in ACCOUNT_BLOCKS:
        lines.append("Account/configuration action is required before another API attempt.")
    elif outcome == "blocked":
        lines.append("Review has not recovered; a cooldown or API error remains.")
    if outcome != "completed":
        lines.append("The queue is incomplete; batch progress is not delivery readiness.")
    if args.attempted == "false" and pending > 0:
        lines.append("No new API attempt was made. This deferred run does not indicate recovery.")
    message = "\n".join(lines) + "\n"
    print(message, end="")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
