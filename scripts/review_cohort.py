"""Persist one fixed recovery cohort without widening normal review windows."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


def item_id(item: dict[str, Any]) -> str:
    return str(item.get("canonical_id") or item.get("id") or "")


def load(path: Path, expected_version: str, *, required: bool = False) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise ValueError("Active recovery cohort manifest is missing") from None
        return None
    except (OSError, ValueError) as exc:
        raise ValueError("Recovery cohort manifest is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Invalid recovery cohort schema")
    if not isinstance(payload.get("cohort_id"), str) or not payload["cohort_id"]:
        raise ValueError("Recovery cohort identity is missing")
    for field in ("original_ids", "active_ids"):
        values = payload.get(field)
        if (not isinstance(values, list)
                or any(not isinstance(value, str) or not value.strip() for value in values)
                or len(values) != len(set(values))):
            raise ValueError(f"Invalid recovery cohort {field}")
    original = set(payload["original_ids"])
    active = set(payload["active_ids"])
    if not original or len(original) > 252 or not active <= original:
        raise ValueError("Recovery cohort extends beyond its fixed original IDs")
    if payload.get("original_count") != len(original):
        raise ValueError("Recovery cohort original count is inconsistent")
    if payload.get("completed_at") and active:
        raise ValueError("Completed recovery cohort still has active IDs")
    # A completed manifest stays inert even after a future review-policy change.
    if active and payload.get("target_review_version") != expected_version:
        raise ValueError("Active recovery cohort review version changed")
    return payload


def retained_items(
    items: Iterable[dict[str, Any]], manifest: dict | None,
    needs_review: Callable[[dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    if manifest is None or not manifest["active_ids"]:
        return []
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        key = item_id(item)
        if key not in by_id or (not needs_review(by_id[key]) and needs_review(item)):
            by_id[key] = item
    missing = set(manifest["active_ids"]) - by_id.keys()
    if missing:
        raise ValueError(f"Recovery cohort has {len(missing)} missing ledger records")
    return [by_id[key] for key in manifest["active_ids"] if needs_review(by_id[key])]


def reconcile(
    path: Path, items: Iterable[dict[str, Any]], checked_at: datetime,
    expected_version: str, needs_review: Callable[[dict[str, Any]], bool],
) -> dict:
    manifest = load(path, expected_version, required=True)
    assert manifest is not None
    remaining = {item_id(item) for item in retained_items(items, manifest, needs_review)}
    # Never rebuild active IDs from the immutable original list.
    updated = dict(manifest)
    updated["active_ids"] = [key for key in manifest["active_ids"] if key in remaining]
    if not updated["active_ids"] and not updated.get("completed_at"):
        updated["completed_at"] = checked_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if updated != manifest:
        path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return updated
