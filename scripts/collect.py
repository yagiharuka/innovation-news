#!/usr/bin/env python3
"""Collect, classify, deduplicate, and publish the World Innovation Brief.

The collector only reads sources declared in config/sources.json.  It does not
discover arbitrary sites, which keeps the daily brief inside the user's source
quality policy.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
from copy import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from openpyxl import load_workbook
from openpyxl.workbook.properties import CalcProperties


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
DOCS_DATA_DIR = DOCS_DIR / "data"
MASTER_CSV = DATA_DIR / "news.csv"
MASTER_JSON = DATA_DIR / "news.json"
RUN_LOG_JSON = DATA_DIR / "run_log.json"
SOURCE_STATUS_JSON = DATA_DIR / "source_status.json"
PUBLIC_JSON = DOCS_DATA_DIR / "news.json"
PUBLIC_SOURCE_STATUS = DOCS_DATA_DIR / "source_status.json"
TEMPLATE_XLSX = ROOT / "assets" / "innovation_news_ledger_template.xlsx"
PUBLIC_XLSX = DOCS_DIR / "innovation_news_ledger.xlsx"

JST = timezone(timedelta(hours=9))
USER_AGENT = (
    "WorldInnovationBrief/1.0 "
    "(RSS reader; contact: repository owner at github.com/yagiharuka/innovation-news)"
)
TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "campaign",
    "cmpid",
}

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Innovation Policy": (
        "innovation policy",
        "science policy",
        "technology policy",
        "industrial policy",
        "research policy",
        "national strategy",
        "strategic plan",
        "regulation",
        "regulatory",
        "legislation",
        "government funding",
        "funding programme",
        "funding program",
        "public investment",
        "subsidy",
        "subsidies",
        "grant programme",
        "grant program",
        "research and development",
        "r&d",
        "export control",
        "standards",
        "procurement",
        "competitiveness",
        "science budget",
        "研究開発",
        "科学技術",
        "産業政策",
        "イノベーション政策",
        "規制",
        "補助金",
        "予算",
        "国家戦略",
    ),
    "Artificial Intelligence": (
        "artificial intelligence",
        "generative ai",
        "foundation model",
        "large language model",
        "machine learning",
        "deep learning",
        "ai model",
        "ai system",
        "ai safety",
        "ai governance",
        "ai chip",
        "compute infrastructure",
        "データセンター",
        "人工知能",
        "生成ai",
        "基盤モデル",
    ),
    "Robotics": (
        "robot",
        "robotics",
        "humanoid",
        "autonomous system",
        "industrial automation",
        "warehouse automation",
        "drone",
        "unmanned system",
        "ロボット",
        "自律システム",
    ),
    "Semiconductors & Telecom": (
        "semiconductor",
        "microelectronics",
        "chipmaking",
        "chip manufacturing",
        "chip fabrication",
        "foundry",
        "wafer",
        "lithography",
        "advanced packaging",
        "photonics",
        "telecom",
        "telecommunications",
        "5g",
        "6g",
        "open ran",
        "radio access network",
        "satellite communications",
        "半導体",
        "通信",
        "先端パッケージ",
    ),
    "Quantum": (
        "quantum",
        "qubit",
        "post-quantum",
        "quantum computing",
        "quantum sensing",
        "quantum communication",
        "量子",
        "量子コンピュー",
    ),
    "Fusion Energy": (
        "fusion energy",
        "fusion power",
        "nuclear fusion",
        "tokamak",
        "stellarator",
        "plasma confinement",
        "tritium",
        "iter",
        "核融合",
        "フュージョンエネルギー",
    ),
    "Biotechnology": (
        "biotechnology",
        "biotech",
        "genomics",
        "genome",
        "gene editing",
        "crispr",
        "synthetic biology",
        "biomanufacturing",
        "bioeconomy",
        "cell therapy",
        "mrna",
        "バイオ",
        "ゲノム",
        "遺伝子編集",
    ),
    "Healthcare": (
        "healthcare",
        "health care",
        "medical",
        "medicine",
        "clinical trial",
        "diagnostic",
        "drug discovery",
        "public health",
        "hospital",
        "vaccine",
        "therapeutic",
        "医療",
        "ヘルスケア",
        "治験",
        "診断",
        "創薬",
    ),
}

POLICY_TERMS = TOPIC_KEYWORDS["Innovation Policy"] + (
    "ministry",
    "department of",
    "commission",
    "congress",
    "parliament",
    "white house",
    "roadmap",
    "initiative",
    "funding opportunity",
    "investment programme",
    "investment program",
    "lawmakers",
    "policy framework",
    "官民",
    "省",
    "庁",
    "政府",
)

REGION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "United States": (
        "united states",
        "u.s.",
        " us ",
        "american",
        "white house",
        "congress",
        "washington",
        "nist",
        "national science foundation",
        "department of energy",
        "silicon valley",
    ),
    "Asia": (
        "china",
        "chinese",
        "japan",
        "japanese",
        "south korea",
        "korean",
        "taiwan",
        "taiwanese",
        "india",
        "singapore",
        "asean",
        "tokyo",
        "beijing",
        "seoul",
        "taipei",
        "meti",
        "tsmc",
        "samsung",
        "中国",
        "日本",
        "韓国",
        "台湾",
        "インド",
        "シンガポール",
    ),
    "EU & Europe": (
        "european union",
        "european commission",
        " eu ",
        "european",
        "germany",
        "france",
        "netherlands",
        "belgium",
        "brussels",
        "united kingdom",
        "britain",
        "uk government",
        "欧州",
        "eu委員会",
    ),
    "Middle East": (
        "middle east",
        "saudi arabia",
        "saudi",
        "united arab emirates",
        "uae",
        "qatar",
        "israel",
        "abu dhabi",
        "dubai",
        "riyadh",
        "neom",
        "g42",
        "mbzuai",
        "kaust",
        "中東",
        "サウジ",
        "アラブ首長国連邦",
        "イスラエル",
    ),
}

CSV_COLUMNS = [
    "collected_at_jst",
    "published_at",
    "region",
    "country",
    "topic",
    "policy_relevance",
    "source_type",
    "source",
    "title",
    "summary",
    "url",
    "canonical_id",
    "first_seen",
    "status",
    "notes",
]

SOURCE_REGISTRY_COLUMNS = [
    "Active",
    "Region",
    "Country / Area",
    "Category",
    "Source",
    "Organization",
    "Feed URL",
    "Homepage",
    "Priority",
    "Native Feed",
    "Notes",
]


@dataclass
class FeedResult:
    source: dict[str, Any]
    entries_seen: int
    entries_kept: int
    status: str
    detail: str
    elapsed_seconds: float


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_jst(value: datetime) -> str:
    return value.astimezone(JST).replace(microsecond=0).isoformat()


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def plain_text(value: str | None, limit: int = 700) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(html.unescape(value), "html.parser")
    text = normalize_space(soup.get_text(" ", strip=True))
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def entry_summary(entry: Any) -> str:
    raw = getattr(entry, "summary", "") or getattr(entry, "description", "")
    if not raw:
        content = getattr(entry, "content", [])
        if isinstance(content, list):
            raw = " ".join(str(part.get("value", "")) for part in content if isinstance(part, dict))
        elif isinstance(content, str):
            raw = content
    return plain_text(str(raw) if raw else "")


def normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^\w\s&+\-]", " ", value)
    return normalize_space(value)


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in TRACKING_PARAMS:
            continue
        query_items.append((key, value))
    query = urlencode(sorted(query_items))
    return urlunsplit((scheme, netloc, path, query, ""))


def title_fingerprint(title: str) -> str:
    text = normalized_text(title)
    boilerplate = {
        "news",
        "update",
        "announces",
        "announcement",
        "official",
        "latest",
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "its",
    }
    tokens = [token for token in re.findall(r"[\w+\-]+", text) if token not in boilerplate]
    stable = " ".join(sorted(tokens[:28]))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]


def canonical_id(url: str, source: str, title: str) -> str:
    stable_url = canonicalize_url(url)
    basis = stable_url or f"{source}|{normalized_text(title)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def contains_keyword(text: str, keyword: str) -> bool:
    lowered = text.casefold()
    needle = keyword.casefold()
    if re.fullmatch(r"[a-z0-9+\-&. ]+", needle):
        if needle.strip() in {"ai", "eu", "us", "5g", "6g"}:
            return re.search(rf"(?<!\w){re.escape(needle.strip())}(?!\w)", lowered) is not None
    return needle in lowered


def classify_topics(text: str, source_category: str = "") -> list[str]:
    combined = f" {normalized_text(text)} {normalized_text(source_category)} "
    matches: list[tuple[str, int]] = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        score = sum(1 for keyword in keywords if contains_keyword(combined, keyword))
        if score:
            matches.append((topic, score))
    matches.sort(key=lambda item: (-item[1], list(TOPIC_KEYWORDS).index(item[0])))
    return [topic for topic, _ in matches]


def classify_region(text: str, default_region: str) -> str:
    combined = f" {normalized_text(text)} "
    scores = {
        region: sum(1 for keyword in keywords if contains_keyword(combined, keyword))
        for region, keywords in REGION_KEYWORDS.items()
    }
    winner, score = max(scores.items(), key=lambda item: item[1])
    if score >= 1:
        return winner
    return default_region or "Global"


def policy_relevance(text: str, source_type: str, priority: int) -> int:
    combined = f" {normalized_text(text)} "
    keyword_hits = sum(1 for term in POLICY_TERMS if contains_keyword(combined, term))
    score = min(3, keyword_hits)
    if source_type in {"Government", "Intergovernmental", "Policy Institute"}:
        score += 1
    if priority >= 5:
        score += 1
    return min(5, score)


def entry_datetime(entry: Any, fallback: datetime) -> datetime:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    for attr in ("published", "updated", "created"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                value = date_parser.parse(str(raw))
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc)
            except (ValueError, TypeError, OverflowError):
                continue
    return fallback


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_master() -> list[dict[str, Any]]:
    if not MASTER_CSV.exists():
        return []
    items: list[dict[str, Any]] = []
    with MASTER_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row["policy_relevance"] = int(row.get("policy_relevance") or 0)
            row["topics"] = [part.strip() for part in row.get("topic", "").split("|") if part.strip()]
            row["canonical_url"] = canonicalize_url(row.get("url", ""))
            row["id"] = row.get("canonical_id", "")
            items.append(row)
    return items


def load_run_log() -> list[dict[str, Any]]:
    if not RUN_LOG_JSON.exists():
        return []
    try:
        with RUN_LOG_JSON.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        return list(payload.get("runs", []))
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def build_item(
    source: dict[str, Any],
    title: str,
    link: str,
    summary: str,
    published: datetime,
    collected_at: datetime,
    extra_text: str = "",
) -> dict[str, Any] | None:
    title = normalize_space(title)
    link = normalize_space(link)
    summary = plain_text(summary)
    if not title or not link:
        return None
    classification_text = " ".join(
        [title, summary, extra_text, source.get("category", "")]
    )
    topics = classify_topics(classification_text, source.get("category", ""))
    if not topics:
        return None
    region = classify_region(classification_text, source.get("region", "Global"))
    item_id = canonical_id(link, source["name"], title)
    return {
        "id": item_id,
        "collected_at_jst": iso_jst(collected_at),
        "published_at": iso_z(published),
        "region": region,
        "country": source.get("country", ""),
        "topic": " | ".join(topics),
        "topics": topics,
        "policy_relevance": policy_relevance(
            classification_text,
            source.get("source_type", ""),
            int(source.get("priority", 3)),
        ),
        "source_type": source.get("source_type", ""),
        "source": source["name"],
        "organization": source.get("organization", source["name"]),
        "title": title,
        "summary": summary,
        "url": link,
        "canonical_url": canonicalize_url(link),
        "canonical_id": item_id,
        "first_seen": iso_jst(collected_at),
        "status": "New",
        "notes": "",
        "source_priority": int(source.get("priority", 3)),
        "title_fingerprint": title_fingerprint(title),
    }


def parse_html_date(node: Any, selector: str, attribute: str, fallback: datetime) -> datetime:
    if not selector:
        return fallback
    date_node = node.select_one(selector)
    if date_node is None:
        return fallback
    raw = date_node.get(attribute, "") if attribute else date_node.get_text(" ", strip=True)
    if not raw:
        return fallback
    try:
        value = date_parser.parse(str(raw))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return fallback


def fetch_source(
    session: requests.Session,
    source: dict[str, Any],
    cutoff: datetime,
    collected_at: datetime,
) -> tuple[list[dict[str, Any]], FeedResult]:
    started = time.monotonic()
    entries_seen = 0
    items: list[dict[str, Any]] = []
    try:
        fetch_mode = source.get("fetch_mode", "feed")
        fetch_url = source.get("listing_url") if fetch_mode == "html" else source["feed_url"]
        response = session.get(fetch_url or source["feed_url"], timeout=(8, 30))
        response.raise_for_status()
        if fetch_mode == "html":
            settings = source.get("html", {})
            soup = BeautifulSoup(response.text, "html.parser")
            nodes = soup.select(settings["item_selector"])
            entries_seen = len(nodes)
            for node in nodes[:40]:
                title_node = node.select_one(settings["title_selector"])
                if settings.get("link_selector") == ":self":
                    link_node = node
                else:
                    link_node = node.select_one(
                        settings.get("link_selector") or settings["title_selector"]
                    )
                if title_node is None or link_node is None:
                    continue
                published = parse_html_date(
                    node,
                    settings.get("date_selector", ""),
                    settings.get("date_attribute", ""),
                    collected_at,
                )
                if published < cutoff:
                    continue
                summary_node = (
                    node.select_one(settings["summary_selector"])
                    if settings.get("summary_selector")
                    else None
                )
                item = build_item(
                    source=source,
                    title=title_node.get_text(" ", strip=True),
                    link=urljoin(response.url, link_node.get("href", "")),
                    summary=summary_node.get_text(" ", strip=True) if summary_node else "",
                    published=published,
                    collected_at=collected_at,
                )
                if item:
                    items.append(item)
        else:
            parsed = feedparser.parse(response.content)
            entries_seen = len(parsed.entries)
            if parsed.bozo and not parsed.entries:
                raise ValueError(str(parsed.bozo_exception))

            for entry in parsed.entries[:40]:
                published = entry_datetime(entry, collected_at)
                if published < cutoff:
                    continue
                extra_text = " ".join(
                    normalize_space(getattr(tag, "term", ""))
                    for tag in getattr(entry, "tags", [])
                )
                item = build_item(
                    source=source,
                    title=getattr(entry, "title", ""),
                    link=getattr(entry, "link", ""),
                    summary=entry_summary(entry),
                    published=published,
                    collected_at=collected_at,
                    extra_text=extra_text,
                )
                if item:
                    items.append(item)

        items.sort(
            key=lambda item: (
                item["published_at"],
                item["policy_relevance"],
                item["source_priority"],
            ),
            reverse=True,
        )
        items = items[:8]
        result = FeedResult(
            source=source,
            entries_seen=entries_seen,
            entries_kept=len(items),
            status="ok",
            detail="",
            elapsed_seconds=round(time.monotonic() - started, 2),
        )
        return items, result
    except Exception as exc:  # one bad source must not stop the whole brief
        detail = normalize_space(f"{type(exc).__name__}: {exc}")[:300]
        result = FeedResult(
            source=source,
            entries_seen=entries_seen,
            entries_kept=0,
            status="error",
            detail=detail,
            elapsed_seconds=round(time.monotonic() - started, 2),
        )
        return [], result


def deduplicate(
    candidates: Iterable[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    seen_ids = {item.get("canonical_id") or item.get("id") for item in existing}
    seen_urls = {canonicalize_url(item.get("url", "")) for item in existing if item.get("url")}
    seen_titles = {title_fingerprint(item.get("title", "")) for item in existing}
    added: list[dict[str, Any]] = []
    duplicates = 0

    for item in candidates:
        item_id = item["canonical_id"]
        url = item["canonical_url"]
        title_key = item["title_fingerprint"]
        if item_id in seen_ids or url in seen_urls or title_key in seen_titles:
            duplicates += 1
            continue
        seen_ids.add(item_id)
        seen_urls.add(url)
        seen_titles.add(title_key)
        added.append(item)
    return added, duplicates


def public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("canonical_id") or item.get("id", ""),
        "published_at": item.get("published_at", ""),
        "collected_at_jst": item.get("collected_at_jst", ""),
        "region": item.get("region", "Global"),
        "country": item.get("country", ""),
        "topics": item.get("topics")
        or [part.strip() for part in item.get("topic", "").split("|") if part.strip()],
        "policy_relevance": int(item.get("policy_relevance") or 0),
        "source_type": item.get("source_type", ""),
        "source": item.get("source", ""),
        "organization": item.get("organization", item.get("source", "")),
        "title": item.get("title", ""),
        "summary": item.get("summary", ""),
        "url": item.get("url", ""),
        "first_seen": item.get("first_seen", ""),
        "status": item.get("status", "New"),
    }


def save_master(items: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ordered = sorted(items, key=lambda item: item.get("published_at", ""), reverse=True)
    temp_path = MASTER_CSV.with_suffix(".csv.tmp")
    with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for item in ordered:
            row = dict(item)
            if isinstance(row.get("topics"), list):
                row["topic"] = " | ".join(row["topics"])
            writer.writerow(row)
    temp_path.replace(MASTER_CSV)


def save_json_outputs(
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    collected_at: datetime,
) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    recent_cutoff = collected_at - timedelta(days=45)
    public_items = [
        public_item(item)
        for item in sorted(items, key=lambda item: item.get("published_at", ""), reverse=True)
        if parse_iso(item.get("published_at", ""), collected_at) >= recent_cutoff
    ][:600]
    payload = {
        "schema_version": 1,
        "updated_at": iso_z(collected_at),
        "updated_at_jst": iso_jst(collected_at),
        "source_policy": "Government, official company, established policy institute, major media, and leading scientific publication allowlist only.",
        "article_count": len(public_items),
        "source_count": sum(1 for source in sources if source.get("active")),
        "items": public_items,
    }
    for path in (MASTER_JSON, PUBLIC_JSON):
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")


def parse_iso(raw: str, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        value = date_parser.parse(raw)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return fallback


def source_status_payload(results: list[FeedResult], collected_at: datetime) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": iso_z(collected_at),
        "summary": {
            "checked": len(results),
            "succeeded": sum(1 for result in results if result.status == "ok"),
            "failed": sum(1 for result in results if result.status != "ok"),
            "entries_seen": sum(result.entries_seen for result in results),
            "entries_kept": sum(result.entries_kept for result in results),
        },
        "sources": [
            {
                "name": result.source["name"],
                "organization": result.source.get("organization", ""),
                "source_type": result.source.get("source_type", ""),
                "region": result.source.get("region", ""),
                "homepage": result.source.get("homepage", ""),
                "feed_url": result.source.get("feed_url", ""),
                "status": result.status,
                "detail": result.detail,
                "entries_seen": result.entries_seen,
                "entries_kept": result.entries_kept,
                "elapsed_seconds": result.elapsed_seconds,
            }
            for result in results
        ],
    }


def save_source_status(results: list[FeedResult], collected_at: datetime) -> None:
    payload = source_status_payload(results, collected_at)
    for path in (SOURCE_STATUS_JSON, PUBLIC_SOURCE_STATUS):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")


def copy_cell_style(source_cell: Any, target_cell: Any) -> None:
    if source_cell.has_style:
        target_cell._style = copy(source_cell._style)
    if source_cell.number_format:
        target_cell.number_format = source_cell.number_format
    if source_cell.alignment:
        target_cell.alignment = copy(source_cell.alignment)
    if source_cell.protection:
        target_cell.protection = copy(source_cell.protection)


def update_workbook(
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    run_log: list[dict[str, Any]],
) -> None:
    if not TEMPLATE_XLSX.exists():
        raise FileNotFoundError(f"Workbook template is missing: {TEMPLATE_XLSX}")
    workbook = load_workbook(TEMPLATE_XLSX)
    ledger = workbook["News Ledger"]
    registry = workbook["Source Registry"]
    log_sheet = workbook["Run Log"]
    dashboard = workbook["Dashboard"]

    max_data_row = max(ledger.max_row, 4)
    for row in ledger.iter_rows(min_row=4, max_row=max_data_row, min_col=1, max_col=15):
        for cell in row:
            cell.value = None

    ordered = sorted(items, key=lambda item: item.get("published_at", ""), reverse=True)
    for row_index, item in enumerate(ordered[:10000], start=4):
        values = [
            item.get("collected_at_jst", ""),
            item.get("published_at", ""),
            item.get("region", ""),
            item.get("country", ""),
            item.get("topic", "")
            or " | ".join(item.get("topics", [])),
            int(item.get("policy_relevance") or 0),
            item.get("source_type", ""),
            item.get("source", ""),
            item.get("title", ""),
            item.get("summary", ""),
            item.get("url", ""),
            item.get("canonical_id") or item.get("id", ""),
            item.get("first_seen", ""),
            item.get("status", "New"),
            item.get("notes", ""),
        ]
        for col_index, value in enumerate(values, start=1):
            target = ledger.cell(row=row_index, column=col_index)
            copy_cell_style(ledger.cell(row=4, column=col_index), target)
            target.value = value
        ledger.cell(row=row_index, column=11).hyperlink = item.get("url", "")
        ledger.row_dimensions[row_index].height = 42

    ledger_last_row = max(4, len(ordered) + 3)
    if "NewsLedgerTable" in ledger.tables:
        ledger.tables["NewsLedgerTable"].ref = f"A3:O{ledger_last_row}"
    ledger.auto_filter.ref = f"A3:O{ledger_last_row}"
    for validation in ledger.data_validations.dataValidation:
        validation.sqref = f"N4:N{ledger_last_row}"

    for row in registry.iter_rows(min_row=4, max_row=max(registry.max_row, 4), min_col=1, max_col=11):
        for cell in row:
            cell.value = None
    for row_index, source in enumerate(sources, start=4):
        values = [
            "Yes" if source.get("active") else "No",
            source.get("region", ""),
            source.get("country", ""),
            source.get("category", ""),
            source.get("name", ""),
            source.get("organization", ""),
            source.get("feed_url", ""),
            source.get("homepage", ""),
            int(source.get("priority", 3)),
            "Yes" if source.get("native_feed") else "No",
            source.get("notes", ""),
        ]
        for col_index, value in enumerate(values, start=1):
            target = registry.cell(row=row_index, column=col_index)
            copy_cell_style(registry.cell(row=4, column=col_index), target)
            target.value = value
        registry.cell(row=row_index, column=7).hyperlink = source.get("feed_url", "")
        registry.cell(row=row_index, column=8).hyperlink = source.get("homepage", "")
        registry.row_dimensions[row_index].height = 36
    if "SourceRegistryTable" in registry.tables:
        registry.tables["SourceRegistryTable"].ref = f"A3:K{max(4, len(sources) + 3)}"
    registry.auto_filter.ref = f"A3:K{max(4, len(sources) + 3)}"

    for row in log_sheet.iter_rows(min_row=4, max_row=max(log_sheet.max_row, 4), min_col=1, max_col=8):
        for cell in row:
            cell.value = None
    for row_index, run in enumerate(run_log[-100:][::-1], start=4):
        values = [
            run.get("run_at_jst", ""),
            int(run.get("feeds_checked", 0)),
            int(run.get("feeds_succeeded", 0)),
            int(run.get("new_items", 0)),
            int(run.get("duplicates_skipped", 0)),
            int(run.get("feed_errors", 0)),
            float(run.get("duration_seconds", 0)),
            run.get("note", ""),
        ]
        for col_index, value in enumerate(values, start=1):
            target = log_sheet.cell(row=row_index, column=col_index)
            copy_cell_style(log_sheet.cell(row=4, column=col_index), target)
            target.value = value
    if "RunLogTable" in log_sheet.tables:
        log_sheet.tables["RunLogTable"].ref = f"A3:H{max(4, len(run_log[-100:]) + 3)}"
    log_sheet.auto_filter.ref = f"A3:H{max(4, len(run_log[-100:]) + 3)}"

    if run_log:
        dashboard["B5"] = run_log[-1].get("run_at_jst", "")
    if workbook.calculation is None:
        workbook.calculation = CalcProperties(calcMode="auto")
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True

    PUBLIC_XLSX.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(PUBLIC_XLSX)


def append_run_log(run: dict[str, Any]) -> list[dict[str, Any]]:
    runs = load_run_log()
    runs.append(run)
    runs = runs[-365:]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_JSON.open("w", encoding="utf-8") as handle:
        json.dump({"schema_version": 1, "runs": runs}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return runs


def ensure_seed_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not MASTER_CSV.exists():
        with MASTER_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.DictWriter(handle, fieldnames=CSV_COLUMNS).writeheader()


def run(max_age_hours: int, initial_days: int) -> int:
    started = time.monotonic()
    ensure_seed_files()
    config = load_config()
    sources = [source for source in config["sources"] if source.get("active")]
    existing = load_master()
    collected_at = now_utc()
    cutoff = collected_at - (
        timedelta(days=initial_days) if not existing else timedelta(hours=max_age_hours)
    )

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
            "Accept-Language": "en-US,en;q=0.8,ja;q=0.5",
        }
    )

    candidates: list[dict[str, Any]] = []
    results: list[FeedResult] = []
    for source in sources:
        items, result = fetch_source(session, source, cutoff, collected_at)
        candidates.extend(items)
        results.append(result)
        print(
            f"[{result.status.upper():5}] {source['name']}: "
            f"seen={result.entries_seen} kept={result.entries_kept} "
            f"{result.elapsed_seconds:.2f}s"
        )

    candidates.sort(
        key=lambda item: (
            item["published_at"],
            item["policy_relevance"],
            item["source_priority"],
        ),
        reverse=True,
    )
    new_items, duplicates = deduplicate(candidates, existing)
    merged = new_items + existing
    merged.sort(key=lambda item: item.get("published_at", ""), reverse=True)

    save_master(merged)
    save_json_outputs(merged, sources, collected_at)
    save_source_status(results, collected_at)

    succeeded = sum(1 for result in results if result.status == "ok")
    errors = len(results) - succeeded
    run_record = {
        "run_at": iso_z(collected_at),
        "run_at_jst": iso_jst(collected_at),
        "feeds_checked": len(results),
        "feeds_succeeded": succeeded,
        "new_items": len(new_items),
        "duplicates_skipped": duplicates,
        "feed_errors": errors,
        "duration_seconds": round(time.monotonic() - started, 2),
        "note": "Initial lookback" if not existing else "Daily update",
    }
    runs = append_run_log(run_record)
    update_workbook(merged, sources, runs)

    print(
        json.dumps(
            {
                "status": "ok",
                "new_items": len(new_items),
                "duplicates_skipped": duplicates,
                "feeds_checked": len(results),
                "feeds_succeeded": succeeded,
                "feed_errors": errors,
                "ledger_items": len(merged),
                "public_json": str(PUBLIC_JSON.relative_to(ROOT)),
                "public_xlsx": str(PUBLIC_XLSX.relative_to(ROOT)),
            },
            ensure_ascii=False,
        )
    )
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-age-hours",
        type=int,
        default=96,
        help="Lookback window after the initial run (default: 96 hours).",
    )
    parser.add_argument(
        "--initial-days",
        type=int,
        default=14,
        help="Lookback window when the ledger is empty (default: 14 days).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        return run(max_age_hours=args.max_age_hours, initial_days=args.initial_days)
    except Exception as exc:
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
