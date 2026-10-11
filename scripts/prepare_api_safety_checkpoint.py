#!/usr/bin/env python3
"""Keep safety-only state aligned with the durable article/cohort checkpoint."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import review_gate


def prepare_state(current: dict, durable: dict) -> dict:
    prepared = dict(current)
    queue_fields = {key for key in current if key.startswith("pending_")}
    queue_fields.update(key for key in durable if key.startswith("pending_"))
    queue_fields.update(("priority_window_hours", "public_items"))
    for key in queue_fields:
        if key in durable:
            prepared[key] = durable[key]
        else:
            prepared.pop(key, None)
    prepared.update(selected=0, reviewed=0, excluded=0,
                    checkpoint_persistence="api_safety_only")

    def status(count: int) -> str:
        if count == 0:
            return "completed"
        kind = prepared.get("api_error_kind", "")
        if kind in {"quota_blocked", "authentication_failed", "access_denied"}:
            return kind
        if prepared.get("rate_limited"):
            return "rate_limited"
        return "review_failed"

    try:
        prepared["status"] = status(review_gate.priority_pending_count(prepared))
        backlog = int(prepared["pending_in_window"])
        if backlog < 0:
            raise ValueError("Invalid durable pending count")
        prepared["backlog_status"] = status(backlog)
    except (KeyError, TypeError, ValueError):
        # Unknown durable queue state must block the next gate, while the
        # separately persisted run log still records the requests just used.
        for key in list(prepared):
            if key.startswith("pending_"):
                prepared.pop(key)
        prepared["status"] = prepared["backlog_status"] = "review_failed"
    return prepared


def main() -> int:
    path = Path("data/review_state.json")
    if not path.exists():
        return 0
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            raise ValueError("Invalid checkpoint")
    except (OSError, ValueError):
        print("API checkpoint is unreadable; preserving it and the usage log for fail-closed recovery.")
        return 0
    result = subprocess.run(
        ["git", "show", "HEAD:data/review_state.json"],
        capture_output=True, text=True, check=False,
    )
    try:
        durable = json.loads(result.stdout) if result.returncode == 0 else {}
        if not isinstance(durable, dict):
            durable = {}
    except ValueError:
        durable = {}
    path.write_text(json.dumps(prepare_state(current, durable), ensure_ascii=False, indent=2)
                    + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
