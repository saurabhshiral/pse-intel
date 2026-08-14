#!/usr/bin/env python3
"""
PSE Intel — Daily News Fetcher
Dependencies: pip install feedparser requests
Runs daily via GitHub Actions.
- Appends new articles to news_history.csv (permanent archive)
- Rewrites news_recent.json (last 90 days, consumed by index.html)
- Rewrites leadership_briefs.json (AI briefing per PSE leader, consumed by Tab 0)
"""
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests

HISTORY_FILE = "news_history.csv"
RECENT_FILE  = "news_recent.json"
BRIEFS_FILE  = "leadership_briefs.json"
SNAPSHOT_FILE = "leadership_snapshot.json"
RECENT_DAYS  = 90
BRIEF_LOOKBACK = 7   # fallback; generate_briefs() uses since-last-Monday window
GROQ_MODEL   = "llama-3.3-70b-versatile"

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


# ── Helpers ───────────────────────────────────────────────────────

def load_existing():
    path = Path(HISTORY_FILE)
    if not path.exists():
        return [], set()
    rows, urls = [], set()
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


# ── Name matching ─────────────────────────────────────────────────

def name_tokens(full_name):
    """Return matchable tokens from a person's name (skip single-letter initials)."""
    return [p.rstrip(".") for p in full_name.split() if len(p.rstrip(".")) > 1]


def article_mentions(article, tokens):
    """True if any name token appears in the headline or snippet."""
    haystack = (article.get("headline", "") + " " + article.get("snippet", "")).lower()
    return any(t.lower() in haystack for t in tokens)


# ── Groq call ────────────────────────────────────────────────────

BRIEF_PROMPT = """You are briefing an Accenture consulting team about {name}, {title} at Puget Sound Energy.

Based on these recent news articles, write:
1. A 2-3 sentence briefing of what has been reported about this person. Be specific and factual.
2. A single sentiment word: positive, neutral, or negative.

Return ONLY valid JSON with exactly these two keys:
{{"briefing": "...", "sentiment": "positive|neutral|negative"}}

Articles:
{articles}"""


def call_groq_brief(name, title, articles, api_key):
    article_text = "\n\n".join(
        f"HEADLINE: {a.get('headline','')}\nDATE: {a.get('pub_date','')}\nSNIPPET: {a.get('snippet','')[:300]}"
        for a in articles[:8]
    )
    prompt = BRIEF_PROMPT.format(name=name, title=title, articles=article_text)
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.2,
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
        return data.get("briefing", ""), data.get("sentiment", "neutral")
    except Exception as e:
        print(f"  WARNING: Groq brief failed for {name}: {e}", file=sys.stderr)
        return "", "neutral"


# ── Leadership briefs ─────────────────────────────────────────────

def generate_briefs(all_rows):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("  GROQ_API_KEY not set — skipping leadership briefs")
        return

    snapshot_path = Path(SNAPSHOT_FILE)
    if not snapshot_path.exists():
        print("  leadership_snapshot.json not found — skipping briefs")
        return

    with open(snapshot_path, encoding="utf-8") as f:
        snapshot = json.load(f)

    leaders = {
        name: info for name, info in snapshot.get("people", {}).items()
        if info.get("source") == "leadership"
    }

    # Window = since last Monday (covers current week however many days in)
    today_dt = datetime.now(timezone.utc)
    days_since_monday = today_dt.weekday()  # Monday=0
    last_monday = today_dt - timedelta(days=days_since_monday)
    cutoff = last_monday.strftime("%Y-%m-%d")
    recent_leadership = [
        r for r in all_rows
        if r.get("section") == "leadership"
        and (r.get("pub_date") or r.get("fetched_date", "")) >= cutoff
    ]

    print(f"  Generating briefs for {len(leaders)} leaders ({len(recent_leadership)} recent articles)")

    briefs = {}
    for name, info in leaders.items():
        tokens = name_tokens(name)
        matched = [a for a in recent_leadership if article_mentions(a, tokens)]

        if matched:
            print(f"    {name}: {len(matched)} article(s) → calling Groq...")
            briefing, sentiment = call_groq_brief(name, info.get("title", ""), matched, api_key)
        else:
            briefing, sentiment = "", "none"

        briefs[name] = {
            "title":         info.get("title", ""),
            "source":        info.get("source", ""),
            "briefing":      briefing,
            "sentiment":     sentiment,
            "article_count": len(matched),
            "articles": [
                {"headline": a.get("headline", ""), "url": a.get("url", ""), "pub_date": a.get("pub_date", ""), "source": a.get("source", "")}
                for a in matched
            ],
        }

    with open(BRIEFS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": BRIEF_LOOKBACK,
            "briefs": briefs,
        }, f, indent=2, ensure_ascii=False)

    covered = sum(1 for b in briefs.values() if b["article_count"] > 0)
    print(f"  Wrote {BRIEFS_FILE}: {covered}/{len(leaders)} leaders with coverage")


# ── Main ──────────────────────────────────────────────────────────

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

    # Append to CSV
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

    # Rewrite news_recent.json
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    all_rows = existing_rows + new_rows
    recent = [r for r in all_rows if (r.get("pub_date") or r.get("fetched_date", "")) >= cutoff]
    recent.sort(key=lambda r: (r.get("pub_date", ""), r.get("fetched_date", "")), reverse=True)

    with open(RECENT_FILE, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "articles": recent}, f, indent=2, ensure_ascii=False)
    print(f"  Wrote {len(recent)} articles to {RECENT_FILE}")

    # Generate per-leader AI briefs
    generate_briefs(all_rows)


if __name__ == "__main__":
    main()
