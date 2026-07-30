#!/usr/bin/env python3
"""Allow historical review only after all fresh candidates are complete."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

import review_gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-budget", type=int, default=1)
    args = parser.parse_args()
    if args.request_budget < 1:
        parser.error("--request-budget must be at least 1")

    state = review_gate.load_json(review_gate.REVIEW_STATE_PATH)
    if review_gate.priority_pending_count(state) > 0:
        state["pending_in_window"] = 0
    result = review_gate.evaluate_review_gate(
        review_gate.load_json(review_gate.RUN_LOG_PATH),
        state,
        now=datetime.now(timezone.utc),
        next_request_budget=args.request_budget,
        backlog_request_budget=20,
        # The legacy gate reserves 50 requests. Increasing the nominal ceiling
        # by the same amount preserves the real 146-request safety ceiling.
        global_request_budget=196,
    )
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(
                f"should_run={'true' if result['should_run'] else 'false'}\n"
            )
    print(
        f"Historical review due={result['should_run']}; "
        f"{result['detail']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
