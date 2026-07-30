#!/usr/bin/env python3
"""Review only daily candidates first seen in the last 24 hours."""

from __future__ import annotations

import collect


original_selector = collect.select_scope_review_items


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


def main() -> int:
    collect.select_scope_review_items = select_fresh_only
    return collect.review_backlog(
        policy_history_days=collect.DEFAULT_POLICY_HISTORY_DAYS,
        technology_history_days=collect.DEFAULT_TECHNOLOGY_HISTORY_DAYS,
    )


if __name__ == "__main__":
    raise SystemExit(main())
