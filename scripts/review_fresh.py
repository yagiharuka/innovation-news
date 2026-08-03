#!/usr/bin/env python3
"""Review the protected morning queue before historical backlog candidates."""

from __future__ import annotations

import collect


original_append_run_log = collect.append_run_log


def select_fresh_only(
    items: list[dict],
    priority_items: list[dict],
    **kwargs: object,
) -> list[dict]:
    """Process the protected queue oldest-first so new runs cannot starve it."""
    del items
    limit = max(0, int(kwargs.get("limit", 0)))
    return sorted(
        priority_items,
        key=collect.fresh_priority_sort_key,
    )[:limit]


def append_fresh_run_log(run: dict) -> list[dict]:
    """Keep fresh-review quota separate from historical backlog quota."""
    run = dict(run)
    run["note"] = "Review fresh candidates"
    return original_append_run_log(run)


def refresh_completed_checkpoint() -> None:
    """Timestamp a verified empty queue even when review_backlog exits early."""
    state = collect.load_review_state()
    if not (
        state.get("status") == "completed"
        and state.get("review_version") == collect.TECH_SCOPE_REVIEW_VERSION
        and int(state.get("pending_in_window") or 0) == 0
        and int(state.get("pending_priority_36h") or 0) == 0
    ):
        return
    checked_at = collect.now_utc()
    state["updated_at"] = collect.iso_z(checked_at)
    state["updated_at_jst"] = collect.iso_jst(checked_at)
    state["public_items"] = len(collect.load_public_payload().get("items", []))
    collect.save_review_state(state)


def main() -> int:
    collect.select_scope_review_items = select_fresh_only
    collect.append_run_log = append_fresh_run_log
    result = collect.review_backlog(
        policy_history_days=collect.DEFAULT_POLICY_HISTORY_DAYS,
        technology_history_days=collect.DEFAULT_TECHNOLOGY_HISTORY_DAYS,
    )
    if result == 0:
        # review_backlog may return "already_complete" before persisting a new
        # timestamp.  The call has nevertheless scanned the current ledger, so
        # refresh the checkpoint used by the morning-mail safety gate.
        refresh_completed_checkpoint()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
