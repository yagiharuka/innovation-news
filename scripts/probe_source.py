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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--days", type=int, default=14)
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
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
