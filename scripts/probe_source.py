#!/usr/bin/env python3
"""Run one configured source through the real collector and persist evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import requests

import collect


ROOT = Path(__file__).resolve().parents[1]


def probe_is_healthy(
    status: str,
    entries_seen: int,
    entries_kept: int,
    min_seen: int = 0,
    min_kept: int = 0,
) -> bool:
    """Distinguish a working zero-result feed from a broken critical probe."""
    return (
        status == "ok"
        and entries_seen >= max(0, min_seen)
        and entries_kept >= max(0, min_kept)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--min-seen", type=int, default=0)
    parser.add_argument("--min-kept", type=int, default=0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    config = json.loads((ROOT / "config" / "sources.json").read_text("utf-8"))
    source = next(
        (row for row in config["sources"] if row.get("name") == args.source),
        None,
    )
    if source is None:
        raise SystemExit(f"Unknown source: {args.source}")

    now = datetime.now(timezone.utc)
    items, result = collect.fetch_source(
        requests.Session(),
        source,
        now - timedelta(days=max(1, args.days)),
        now,
    )
    payload = {
        "updated_at": now.isoformat(),
        "source": args.source,
        "status": result.status,
        "detail": result.detail,
        "entries_seen": result.entries_seen,
        "entries_kept": result.entries_kept,
        "minimums": {
            "entries_seen": max(0, args.min_seen),
            "entries_kept": max(0, args.min_kept),
        },
        "sample": [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "published_at": item.get("published_at", ""),
            }
            for item in items[:10]
        ],
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False))
    healthy = probe_is_healthy(
        result.status,
        result.entries_seen,
        result.entries_kept,
        args.min_seen,
        args.min_kept,
    )
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
