from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Dict, Any

import feedparser
import pandas as pd


# ----------------------------
# Scoring rules
# ----------------------------
POSITIVE_KEYWORDS = [
    "one-time", "one time", "quick", "small", "simple",
    "excel", "spreadsheet", "csv", "data cleanup", "data cleaning",
    "python script", "automation", "data extraction", "web scraping",
    "extract data",
]

NEGATIVE_KEYWORDS = [
    "real-time", "realtime", "production-ready", "production ready",
    "long-term", "long term", "ongoing", "enterprise", "scalable",
    "compliance", "emails", "phone numbers", "leads", "scrape contacts",
    "linkedin", "apollo", "hunter.io", "captcha", "login",
]

# prioritize fixed-price-style language
FIXED_PRICE_HINTS = ["fixed price", "budget", "deliverable", "milestone"]


@dataclass(frozen=True)
class FeedItem:
    title: str
    link: str
    published: str
    summary: str
    source_feed: str


def normalize_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def score_item(title: str, summary: str) -> int:
    text = normalize_text(f"{title} {summary}")
    score = 0

    # positives
    for kw in POSITIVE_KEYWORDS:
        if kw in text:
            score += 2

    # negatives (heavier penalty)
    for kw in NEGATIVE_KEYWORDS:
        if kw in text:
            score -= 4

    # small hint boosts
    for kw in FIXED_PRICE_HINTS:
        if kw in text:
            score += 1

    # Extra boost if clearly Excel-only
    if "excel" in text and "python" not in text:
        score += 1

    return score


def parse_feed(url: str) -> List[FeedItem]:
    parsed = feedparser.parse(url)
    items: List[FeedItem] = []

    feed_title = parsed.feed.get("title", url)

    for e in parsed.entries:
        title = e.get("title", "").strip()
        link = e.get("link", "").strip()
        published = e.get("published", "") or e.get("updated", "")
        summary = e.get("summary", "") or e.get("description", "")
        if not title or not link:
            continue
        items.append(
            FeedItem(
                title=title,
                link=link,
                published=published,
                summary=summary,
                source_feed=feed_title,
            )
        )
    return items


def dedupe(items: Iterable[FeedItem]) -> List[FeedItem]:
    seen = set()
    out = []
    for it in items:
        key = it.link.split("?")[0]  # normalize querystring differences
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def to_dataframe(items: List[FeedItem]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for it in items:
        s = score_item(it.title, it.summary)
        rows.append(
            {
                "score": s,
                "title": it.title,
                "published": it.published,
                "source_feed": it.source_feed,
                "link": it.link,
                "summary": re.sub(r"\s+", " ", (it.summary or "")).strip(),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(["score", "published"], ascending=[False, False]).reset_index(drop=True)
    return df


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Collect Upwork RSS feeds, dedupe, score, and export to CSV."
    )
    ap.add_argument(
        "--feeds",
        type=str,
        required=True,
        help="Path to a text file with one RSS feed URL per line.",
    )
    ap.add_argument(
        "--out",
        type=str,
        default="upwork_jobs.csv",
        help="Output CSV path.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max total items to keep after sorting (top N).",
    )
    args = ap.parse_args()

    feeds_path = Path(args.feeds)
    if not feeds_path.exists():
        print(f"ERROR: feeds file not found: {feeds_path}", file=sys.stderr)
        return 2

    urls = [line.strip() for line in feeds_path.read_text(encoding="utf-8").splitlines()]
    urls = [u for u in urls if u and not u.startswith("#")]
    if not urls:
        print("ERROR: no feed URLs found in feeds file.", file=sys.stderr)
        return 2

    all_items: List[FeedItem] = []
    for url in urls:
        try:
            items = parse_feed(url)
            all_items.extend(items)
        except Exception as ex:
            print(f"WARNING: failed to parse feed {url}: {ex}", file=sys.stderr)

    all_items = dedupe(all_items)
    df = to_dataframe(all_items)
    if df.empty:
        print("No items found. Check your RSS URLs.", file=sys.stderr)
        return 1

    df = df.head(args.limit)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8")

    print(f"Saved {len(df)} items to: {out_path.resolve()}")
    print("Tip: sort by 'score' descending, then open links for the top items.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
