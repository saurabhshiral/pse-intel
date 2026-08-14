#!/usr/bin/env python3
"""
PSE Intel — Daily News Fetcher
Dependencies: pip install feedparser
Runs daily via GitHub Actions.
- Appends new articles to news_history.csv (permanent archive)
- Rewrites news_recent.json (last 90 days, consumed by index.html)
"""
import csv
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser

HISTORY_FILE = "news_history.csv"
RECENT_FILE  = "news_recent.json"
RECENT_DAYS  = 90

FEEDS = {
    "leadership": [
        "https://news.google.com/rss/search?q=Puget+Sound+Energy+leadership+executive+director&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=Puget+Sound+Energy+appointed+promoted+resigned&hl=en-US&gl=US&ceid=US:en",
    ],
    "company": [
        "https://news.google.com/rss/search?q=%22Puget+Sound+Energy%22+news&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=PSE+utility+Washington+rate+grid+renewable&hl=en-US&gl=US&ceid=US:en",
    ],
    "sector": {
        "PacifiCorp":             "https://news.google.com/rss/search?q=PacifiCorp+OR+%22Pacific+Power%22+utility&hl=en-US&gl=US&ceid=US:en",
        "Portland General":       "https://news.google.com/rss/search?q=%22Portland+General+Electric%22+news&hl=en-US&gl=US&ceid=US:en",
        "Avangrid":               "https://news.google.com/rss/search?q=Avangrid+utility+news&hl=en-US&gl=US&ceid=US:en",
        "NV Energy":              "https://news.google.com/rss/search?q=%22NV+Energy%22+utility+news&hl=en-US&gl=US&ceid=US:en",
        "Arizona Public Service": "https://news.google.com/rss/search?q=%22Arizona+Public+Service%22+APS+utility&hl=en-US&gl=US&ceid=US:en",
        "Xcel Energy":            "https://news.google.com/rss/search?q=%22Xcel+Energy%22+utility+news&hl=en-US&gl=US&ceid=US:en",
        "Evergy":                 "https://news.google.com/rss/search?q=Evergy+utility+news&hl=en-US&gl=US&ceid=US:en",
        "Idaho Power":            "https://news.google.com/rss/search?q=%22Idaho+Power%22+utility+news&hl=en-US&gl=US&ceid=US:en",
    },
}

COLUMNS = ["fetched_date", "pub_date", "section", "utility", "source", "headline", "url", "snippet"]


def load_existing():
    path = Path(HISTORY_FILE)
    if not path.exists():
        return [], set()
    rows = []
    urls = set()
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
            urls.add(row.get("url", ""))
    return rows, urls


def parse_pub_date(entry):
    try:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def fetch_feed(url):
    try:
        return feedparser.parse(url).entries
    except Exception as e:
        print(f"  WARNING: {url}: {e}", file=sys.stderr)
        return []


def clean(text):
    import re
    return re.sub(r"<[^>]+>", "", text or "").strip().replace("\n", " ")


def build_row(entry, section, utility, today):
    return {
        "fetched_date": today,
        "pub_date":     parse_pub_date(entry),
        "section":      section,
        "utility":      utility,
        "source":       entry.get("source", {}).get("title", ""),
        "headline":     clean(entry.get("title", "")),
        "url":          entry.get("link", ""),
        "snippet":      clean(entry.get("summary", ""))[:500],
    }


def main():
    print("PSE Intel — Daily News Fetcher")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing_rows, existing_urls = load_existing()
    print(f"  Existing articles: {len(existing_rows)}")

    new_rows = []

    for section, feeds in [("leadership", FEEDS["leadership"]), ("company", FEEDS["company"])]:
        for url in feeds:
            for entry in fetch_feed(url):
                link = entry.get("link", "")
                if not link or link in existing_urls:
                    continue
                new_rows.append(build_row(entry, section, "", today))
                existing_urls.add(link)

    for utility, url in FEEDS["sector"].items():
        for entry in fetch_feed(url):
            link = entry.get("link", "")
            if not link or link in existing_urls:
                continue
            new_rows.append(build_row(entry, "sector", utility, today))
            existing_urls.add(link)

    # Append new rows to CSV (write header if file is new or empty)
    if new_rows:
        path = Path(HISTORY_FILE)
        write_header = not path.exists() or path.stat().st_size == 0
        with open(path, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)
        print(f"  Added {len(new_rows)} new articles")
    else:
        print("  No new articles today")

    # Rewrite news_recent.json (last RECENT_DAYS days, newest first)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    all_rows = existing_rows + new_rows
    recent = [r for r in all_rows if (r.get("pub_date") or r.get("fetched_date", "")) >= cutoff]
    recent.sort(key=lambda r: (r.get("pub_date", ""), r.get("fetched_date", "")), reverse=True)

    with open(RECENT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "articles": recent,
        }, f, indent=2, ensure_ascii=False)

    print(f"  Wrote {len(recent)} articles to {RECENT_FILE} (last {RECENT_DAYS} days)")


if __name__ == "__main__":
    main()
