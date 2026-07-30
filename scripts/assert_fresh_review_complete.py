#!/usr/bin/env python3
"""Fail when the current morning batch has not finished strict review."""

from __future__ import annotations

import json
from pathlib import Path

import review_gate


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "review_state.json"


def main() -> int:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        pending = review_gate.priority_pending_count(state)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Fresh review state is unavailable: {exc}")
        return 1

    if pending:
        print(
            f"Fresh review is incomplete: {pending} candidate(s) remain. "
            "The 30-minute catch-up will continue before email delivery."
        )
        return 1

    print("Fresh review is complete: 0 candidates remain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
