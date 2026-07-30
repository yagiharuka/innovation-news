#!/usr/bin/env python3
"""Gate a catch-up run using only the fresh-candidate pending count."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

import review_gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--request-budget", type=int, default=2)
    args = parser.parse_args()
    if args.request_budget < 1:
        parser.error("--request-budget must be at least 1")

    state = review_gate.load_json(review_gate.REVIEW_STATE_PATH)
    state["pending_in_window"] = state.get("pending_fresh_24h", 0)
    result = review_gate.evaluate_review_gate(
        review_gate.load_json(review_gate.RUN_LOG_PATH),
        state,
        now=datetime.now(timezone.utc),
        force=args.force,
        next_request_budget=args.request_budget,
        # The legacy gate reserves 50 requests for schedules that are now
        # paused or already included in the morning run.
        global_request_budget=196,
    )
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(
                f"should_run={'true' if result['should_run'] else 'false'}\n"
            )
    print(
        f"Fresh candidate review due={result['should_run']}; "
        f"{result['detail']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
