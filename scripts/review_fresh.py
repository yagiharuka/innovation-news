#!/usr/bin/env python3
"""Review only daily candidates first seen in the last 24 hours."""

from __future__ import annotations

import collect


original_selector = collect.select_scope_review_items
original_append_run_log = collect.append_run_log


def select_fresh_only(
    items: list[dict],
    fresh_items: list[dict],
    **kwargs: object,
) -> list[dict]:
    """Keep the resumable selector, but remove historical backlog candidates."""
    del items
    return original_selector(
        fresh_items,
        fresh_items,
        **kwargs,
    )


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
