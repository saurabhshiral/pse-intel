#!/usr/bin/env python3
"""
PSE Intel — Weekly Email Digest
Dependencies: pip install requests
Reads articles from news_history.csv (written by daily_fetch.py).
Set env vars: GROQ_API_KEY, GMAIL_EMAIL, GMAIL_APP_PASSWORD
"""
import csv
import json
import os
import smtplib
import sys
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

CONFIG = {
    "groq_api_key": os.getenv("GROQ_API_KEY"),
    "groq_model": "llama-3.3-70b-versatile",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": os.getenv("GMAIL_EMAIL"),
    "smtp_password": os.getenv("GMAIL_APP_PASSWORD"),
    "recipients": ["sourabh.shiral@gmail.com", "saurabh.s.shiral@accenture.com"],
    "lookback_days": 7,
}

SECTION_MAP = {
    "PSE Leadership & People": "leadership",
    "PSE Company News":        "company",
    "US Utilities Pulse":      "sector",
}


# ── Read articles from CSV ────────────────────────────────────────

def load_articles_from_csv(section_key, lookback_days):
    path = Path("news_history.csv")
    if not path.exists():
        print(f"  WARNING: news_history.csv not found — run daily_fetch.py first", file=sys.stderr)
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    articles = []
    seen_urls = set()

    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("section") != section_key:
                continue
            if row.get("pub_date", "9999") < cutoff:
                continue
            url = row.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            articles.append(row)

    articles.sort(key=lambda r: r.get("pub_date", ""), reverse=True)
    return articles


def build_articles_text(articles):
    if not articles:
        return "No articles found for this period."
    lines = []
    for a in articles[:20]:
        lines.append(f"HEADLINE: {a.get('headline', '')}")
        if a.get("snippet"):
            lines.append(f"SNIPPET: {a['snippet'][:300]}")
        if a.get("utility"):
            lines.append(f"UTILITY: {a['utility']}")
        lines.append(f"DATE: {a.get('pub_date', '')}")
        lines.append(f"URL: {a.get('url', '')}")
        lines.append("")
    return "\n".join(lines)


# ── Groq ──────────────────────────────────────────────────────────

GROQ_PROMPT = """You are briefing an Accenture consulting team on Puget Sound Energy and the US utility sector.

For the section "{section}", write an HTML digest. For each article:
1. Use <h3> for the article headline
2. Use <p> for a one-sentence factual summary of the article
3. Use <ul><li><strong>Why it matters:</strong> One sentence on the consulting relevance</li></ul>

Rules:
- Group near-duplicate stories into one entry
- Skip low-relevance or off-topic articles
- No markdown, no preamble, return only the HTML fragment
- Keep each summary tight — one sentence each"""


def call_groq(section_name, articles_text):
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": CONFIG["groq_model"],
        "messages": [
            {"role": "system", "content": GROQ_PROMPT.format(section=section_name)},
            {"role": "user", "content": articles_text},
        ],
        "max_tokens": 1500,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {CONFIG['groq_api_key']}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=45)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        detail = ""
        try:
            detail = resp.text[:400]
        except Exception:
            pass
        print(f"  ERROR: Groq failed for '{section_name}': {e} | {detail}", file=sys.stderr)
        return "<p><em>Summary unavailable — check Groq API key and quota.</em></p>"


# ── Email builder ─────────────────────────────────────────────────

def build_html_email(sections_html, leadership_data, changes):
    teal = "#0f6e56"
    now = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # Leadership changes alert
    changes_block = ""
    if changes:
        items = ""
        for c in changes:
            if c["type"] == "added":
                items += f"<li><strong style='color:#16a34a'>ADDED</strong>: {c['name']} — {c.get('new_title','')}</li>"
            elif c["type"] == "removed":
                items += f"<li><strong style='color:#dc2626'>REMOVED</strong>: {c['name']} — was {c.get('old_title','')}</li>"
            else:
                items += (
                    f"<li><strong style='color:#d97706'>CHANGED</strong>: {c['name']} — "
                    f"{c.get('old_title','')} &rarr; {c.get('new_title','')}</li>"
                )
        changes_block = f"""
        <div style="background:#fff8f0;border-left:4px solid #dc2626;padding:16px 20px;margin-bottom:28px;border-radius:4px;">
          <h2 style="color:#dc2626;margin:0 0 10px;font-size:16px;font-weight:700;">&#9888; LEADERSHIP CHANGES ALERT</h2>
          <ul style="margin:0;padding-left:20px;line-height:1.9;">{items}</ul>
        </div>"""

    # News sections
    sections_block = ""
    for title, content in sections_html.items():
        sections_block += f"""
        <div style="margin-bottom:32px;">
          <h2 style="color:{teal};border-bottom:2px solid {teal};padding-bottom:6px;margin:0 0 16px;font-size:17px;">{title}</h2>
          <div style="font-size:14px;line-height:1.7;color:#374151;">{content}</div>
        </div>"""

    # Leadership table — split into two sections, leadership sorted by seniority
    leadership_table = ""
    if leadership_data:
        def title_rank(title):
            t = (title or "").lower()
            if "president and chief" in t or "chief executive" in t: return 0
            if "president" in t: return 1
            if t.startswith("svp") or "senior vice president" in t: return 2
            if t.startswith("chief") or "cio" in t or "cfo" in t or "coo" in t: return 3
            if t.startswith("vp") or "vice president" in t: return 4
            return 5

        all_people = leadership_data.get("people", {}).items()
        lteam = sorted(
            [(n, i) for n, i in all_people if i.get("source") == "leadership"],
            key=lambda x: (title_rank(x[1].get("title", "")), x[0].split()[-1].lower())
        )
        board = sorted(
            [(n, i) for n, i in leadership_data.get("people", {}).items() if i.get("source") == "board"],
            key=lambda x: x[0].split()[-1].lower()
        )

        def make_rows(people_list):
            return "".join(
                f"<tr>"
                f"<td style='padding:9px 14px;border-bottom:1px solid #e5e8ec;font-weight:600;color:#0a0f1e'>{name}</td>"
                f"<td style='padding:9px 14px;border-bottom:1px solid #e5e8ec;color:#6b7280'>{info.get('title','—')}</td>"
                f"</tr>"
                for name, info in people_list
            )

        thead = "<thead><tr style='background:#f8f9fb;'><th style='padding:9px 14px;text-align:left;font-weight:600;color:#374151'>Name</th><th style='padding:9px 14px;text-align:left;font-weight:600;color:#374151'>Title</th></tr></thead>"
        table_style = "width:100%;border-collapse:collapse;font-size:13px;background:#fff;border:1px solid #e5e8ec;margin-bottom:16px;"

        leadership_table = f"""
        <div style="margin-bottom:32px;">
          <h2 style="color:{teal};border-bottom:2px solid {teal};padding-bottom:6px;margin:0 0 16px;font-size:17px;">Current PSE Team</h2>
          <p style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;font-family:monospace">Leadership Team</p>
          <table style="{table_style}">{thead}<tbody>{make_rows(lteam)}</tbody></table>
          <p style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;font-family:monospace">Board of Directors</p>
          <table style="{table_style}">{thead}<tbody>{make_rows(board)}</tbody></table>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  h3 {{ margin: 16px 0 4px; font-size: 15px; color: #0a0f1e; }}
  p  {{ margin: 0 0 4px; }}
  ul {{ margin: 2px 0 14px 0; padding-left: 20px; }}
  li {{ color: #374151; }}
</style>
</head>
<body style="font-family:Arial,Helvetica,sans-serif;background:#f8f9fb;margin:0;padding:24px 0;">
  <div style="max-width:680px;margin:0 auto;background:#fff;border:1px solid #e5e8ec;border-radius:8px;overflow:hidden;">
    <div style="background:{teal};padding:28px 32px;">
      <h1 style="color:#fff;margin:0 0 4px;font-size:22px;font-weight:700;">PSE Intel — Weekly Digest</h1>
      <p style="color:rgba(255,255,255,0.75);margin:0;font-size:13px;">{now} &middot; Accenture AMS Team</p>
    </div>
    <div style="padding:32px;">
      {changes_block}
      {sections_block}
      {leadership_table}
      <p style="color:#9ca3af;font-size:11px;margin-top:32px;padding-top:16px;border-top:1px solid #e5e8ec;">
        Generated by PSE Intel &middot; Accenture AMS &middot; Do not reply to this email.
      </p>
    </div>
  </div>
</body>
</html>"""


# ── Email send ────────────────────────────────────────────────────

def send_email(html_content, subject):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = CONFIG["smtp_user"]
    msg["To"] = ", ".join(CONFIG["recipients"])
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    with smtplib.SMTP(CONFIG["smtp_host"], CONFIG["smtp_port"]) as server:
        server.ehlo()
        server.starttls()
        server.login(CONFIG["smtp_user"], CONFIG["smtp_password"])
        server.sendmail(CONFIG["smtp_user"], CONFIG["recipients"], msg.as_string())
    print(f"  Sent to: {', '.join(CONFIG['recipients'])}")


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("PSE Intel — Weekly Digest")

    if not CONFIG["groq_api_key"]:
        print("ERROR: GROQ_API_KEY not set", file=sys.stderr); sys.exit(1)
    if not CONFIG["smtp_user"] or not CONFIG["smtp_password"]:
        print("ERROR: GMAIL_EMAIL / GMAIL_APP_PASSWORD not set", file=sys.stderr); sys.exit(1)

    # Leadership snapshot
    leadership_data, changes = None, []
    if Path("leadership_snapshot.json").exists():
        with open("leadership_snapshot.json", encoding="utf-8") as f:
            leadership_data = json.load(f)
        changes = leadership_data.get("changes", [])
        print(f"  Leadership: {len(leadership_data.get('people',{}))} people, {len(changes)} changes")

    # Build sections from CSV history
    sections_html = {}
    for section_label, section_key in SECTION_MAP.items():
        print(f"\n  [{section_label}]")
        articles = load_articles_from_csv(section_key, CONFIG["lookback_days"])
        print(f"    {len(articles)} articles from last {CONFIG['lookback_days']} days")
        articles_text = build_articles_text(articles)
        print(f"    Calling Groq...")
        sections_html[section_label] = call_groq(section_label, articles_text)

    # Send
    subject = f"PSE Intel Weekly Digest — {datetime.now(timezone.utc).strftime('%B %d, %Y')}"
    if changes:
        subject = f"[ALERT] {subject}"

    html = build_html_email(sections_html, leadership_data, changes)
    print(f"\n  Sending: {subject}")
    send_email(html, subject)
    print("\nDone.")


if __name__ == "__main__":
    main()
