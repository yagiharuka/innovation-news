#!/usr/bin/env python3
"""Audit every active source endpoint and every published article URL."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
PUBLIC_NEWS_PATH = ROOT / "docs" / "data" / "news.json"
OUTPUT_PATH = ROOT / "data" / "url_audit.json"
PUBLIC_OUTPUT_PATH = ROOT / "docs" / "data" / "url_audit.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
ACCESS_RESTRICTED = {401, 403, 406, 418, 451}
DEAD = {404, 410}
TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504}


def classify_status(status_code: int) -> str:
    if 200 <= status_code < 400:
        return "live"
    if status_code in ACCESS_RESTRICTED:
        return "access_restricted"
    if status_code == 429:
        return "rate_limited"
    if status_code in DEAD:
        return "dead"
    if 400 <= status_code < 500:
        return "other_4xx"
    if status_code >= 500:
        return "transient_error"
    return "unknown"


def load_targets() -> list[dict[str, str]]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    sources = config.get("sources", config)
    targets: dict[str, dict[str, str]] = {}
    for source in sources:
        if not source.get("active", True):
            continue
        for field in (
            "feed_url",
            "listing_url",
            "homepage",
            "api_url",
            "proxy_sitemap_url",
        ):
            url = str(source.get(field) or "").strip()
            if not url.startswith(("https://", "http://")):
                continue
            targets.setdefault(
                url,
                {
                    "url": url,
                    "kind": "source",
                    "name": str(source.get("name") or ""),
                    "field": field,
                },
            )
        for url in source.get("proxy_sitemap_urls", []):
            url = str(url or "").strip()
            if not url.startswith(("https://", "http://")):
                continue
            targets.setdefault(
                url,
                {
                    "url": url,
                    "kind": "source",
                    "name": str(source.get("name") or ""),
                    "field": "proxy_sitemap_urls",
                },
            )

    news = json.loads(PUBLIC_NEWS_PATH.read_text(encoding="utf-8"))
    articles = news.get("items", news)
    for article in articles:
        url = str(article.get("url") or "").strip()
        if not url.startswith(("https://", "http://")):
            continue
        targets.setdefault(
            url,
            {
                "url": url,
                "kind": "article",
                "name": str(article.get("source") or ""),
                "field": "url",
            },
        )
    return list(targets.values())


def check_target(target: dict[str, str], timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/rss+xml,"
            "application/xml;q=0.9,*/*;q=0.5"
        ),
        "Range": "bytes=0-4095",
    }
    last_error = ""
    for attempt in range(2):
        try:
            with requests.get(
                target["url"],
                headers=headers,
                timeout=(6, timeout),
                allow_redirects=True,
                stream=True,
            ) as response:
                category = classify_status(response.status_code)
                if category == "transient_error" and attempt == 0:
                    time.sleep(1.0)
                    continue
                return {
                    **target,
                    "category": category,
                    "status_code": response.status_code,
                    "final_url": response.url,
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "error": "",
                }
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                time.sleep(1.0)
                continue
    return {
        **target,
        "category": "transient_error",
        "status_code": 0,
        "final_url": "",
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "error": last_error[:500],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    workers = max(1, min(args.workers, 24))
    timeout = max(5.0, min(args.timeout, 30.0))
    targets = load_targets()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(check_target, target, timeout): target
            for target in targets
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: (row["category"], row["kind"], row["name"], row["url"]))
    counts: dict[str, int] = {}
    for row in results:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"total": len(results), **counts},
        "interpretation": {
            "dead": "Confirmed HTTP 404 or 410 after retry.",
            "access_restricted": "The URL may work in a browser but blocks automated checks.",
            "rate_limited": "Temporary HTTP 429; not treated as a dead link.",
            "transient_error": "Timeout, TLS/DNS, or server error; retry required.",
        },
        "results": results,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    PUBLIC_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
