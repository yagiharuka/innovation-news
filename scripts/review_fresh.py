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


def main() -> int:
    collect.select_scope_review_items = select_fresh_only
    collect.append_run_log = append_fresh_run_log
    return collect.review_backlog(
        policy_history_days=collect.DEFAULT_POLICY_HISTORY_DAYS,
        technology_history_days=collect.DEFAULT_TECHNOLOGY_HISTORY_DAYS,
    )


if __name__ == "__main__":
    raise SystemExit(main())
