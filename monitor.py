#!/usr/bin/env python3
"""
PSE Intel — Leadership Monitor
Dependencies: pip install requests beautifulsoup4
Run locally or via GitHub Actions to track PSE leadership changes.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

LEADERSHIP_URL = "https://www.pse.com/en/about-us/leadership"
BOARD_URL = "https://www.pse.com/en/about-us/board-of-directors"
SNAPSHOT_FILE = "leadership_snapshot.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PSEIntelBot/1.0)"}


def is_person_name(text):
    """Return True only if text looks like a real person name.

    Strategy: require 2-5 words, each starting with a capital, and no
    multi-letter word is ALL-CAPS (catches QUICK LINKS, HYDRO LICENSING, etc.)
    """
    import re
    words = text.split()
    if not (2 <= len(words) <= 5):
        return False
    for w in words:
        core = w.rstrip(".,")
        if not core or not core[0].isupper():
            return False
        # A word with 2+ letters must NOT be all-uppercase (nav/section headers are all-caps)
        if len(core) > 1 and core == core.upper():
            return False
        # Only letters, hyphens, apostrophes, with optional trailing period
        if not re.match(r"^[A-Za-z][A-Za-z\-']*\.?$", w):
            return False
    return True


def scrape_people(url, source_label):
    """Scrape name+title pairs from a PSE leadership/board page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  WARNING: Failed to fetch {url}: {e}", file=sys.stderr)
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    people = {}

    for h4 in soup.find_all("h4"):
        name = h4.get_text(strip=True)
        if not name or not is_person_name(name):
            continue
        title = ""
        parent = h4.parent
        if parent:
            # PSE leadership page uses <div class="h7"> for title
            h7 = parent.find("div", class_="h7")
            if h7:
                title = h7.get_text(strip=True)
        # Fallback: next sibling <p>
        if not title:
            sib = h4.find_next_sibling()
            if sib and sib.name == "p":
                title = sib.get_text(strip=True)
        people[name] = {"title": title, "source": source_label}

    return people


def load_snapshot(path):
    if Path(path).exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def diff_people(old_people, new_people):
    changes = []
    old_names = set(old_people)
    new_names = set(new_people)

    for name in sorted(new_names - old_names):
        changes.append({
            "type": "added",
            "name": name,
            "new_title": new_people[name]["title"],
            "old_title": None,
        })

    for name in sorted(old_names - new_names):
        changes.append({
            "type": "removed",
            "name": name,
            "old_title": old_people[name]["title"],
            "new_title": None,
        })

    for name in sorted(old_names & new_names):
        if old_people[name]["title"] != new_people[name]["title"]:
            changes.append({
                "type": "changed",
                "name": name,
                "old_title": old_people[name]["title"],
                "new_title": new_people[name]["title"],
            })

    return changes


HISTORY_FILE = "leadership_history.json"


def append_history(changes, checked_at):
    """Append change events with timestamps to leadership_history.json."""
    if not changes:
        return
    path = Path(HISTORY_FILE)
    history = []
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARNING: {HISTORY_FILE} unreadable ({e}), starting fresh", file=sys.stderr)
            history = []
    for c in changes:
        history.append({**c, "date": checked_at})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    print(f"  Appended {len(changes)} event(s) to {HISTORY_FILE}")


def main():
    print("PSE Intel — Leadership Monitor")
    print(f"  Fetching {LEADERSHIP_URL}")
    leadership = scrape_people(LEADERSHIP_URL, "leadership")
    print(f"  Found {len(leadership)} people on leadership page")

    print(f"  Fetching {BOARD_URL}")
    board = scrape_people(BOARD_URL, "board")
    print(f"  Found {len(board)} people on board page")

    # Merge; leadership takes precedence for duplicate names
    current = {**board, **leadership}
    print(f"  Total unique people: {len(current)}")

    existing = load_snapshot(SNAPSHOT_FILE)
    old_people = existing.get("people", {}) if existing else {}

    changes = diff_people(old_people, current) if existing else []
    checked_at = datetime.now(timezone.utc).isoformat()

    def title_rank(title):
        t = (title or "").lower()
        if "president and chief" in t or "chief executive" in t: return 0
        if "president" in t: return 1
        if t.startswith("svp") or "senior vice president" in t: return 2
        if t.startswith("chief") or "cio" in t or "cfo" in t or "coo" in t: return 3
        if t.startswith("vp") or "vice president" in t: return 4
        return 5

    # Sort: leadership (by seniority rank, then last name), then board (alphabetical)
    def sort_key(item):
        name, info = item
        last = name.split()[-1].lower()
        if info["source"] == "leadership":
            return (0, title_rank(info["title"]), last)
        return (1, 0, last)

    sorted_people = dict(sorted(current.items(), key=sort_key))

    snapshot = {
        "checked_at": checked_at,
        "people": sorted_people,
        "changes": changes,
    }

    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    print(f"\nSnapshot saved to {SNAPSHOT_FILE}")

    # Persist change events to history file with date
    append_history(changes, checked_at)

    if changes:
        print(f"\n{'=' * 40}")
        print(f"CHANGES DETECTED ({len(changes)}):")
        for c in changes:
            if c["type"] == "added":
                print(f"  + ADDED:   {c['name']} ({c['new_title']})")
            elif c["type"] == "removed":
                print(f"  - REMOVED: {c['name']} (was: {c['old_title']})")
            else:
                print(f"  ~ CHANGED: {c['name']}")
                print(f"      was: {c['old_title']}")
                print(f"      now: {c['new_title']}")
        print("=" * 40)
        sys.exit(1)
    else:
        print("No changes detected.")
        sys.exit(0)


if __name__ == "__main__":
    main()
