#!/usr/bin/env python3
"""
PSE Intel — Weekly Email Digest
Dependencies: pip install requests feedparser
Set env vars: GROQ_API_KEY, OUTLOOK_EMAIL, OUTLOOK_PASSWORD
"""
import json
import os
import smtplib
import sys
import time
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import feedparser
import requests

CONFIG = {
    "groq_api_key": os.getenv("GROQ_API_KEY"),
    "groq_model": "llama3-70b-8192",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": os.getenv("GMAIL_EMAIL"),
    "smtp_password": os.getenv("GMAIL_APP_PASSWORD"),
    "recipients": ["sourabh.shiral@gmail.com", "saurabh.s.shiral@accenture.com"],
    "lookback_days": 7,
}

FEEDS = {
    "PSE Leadership & People": [
        "https://news.google.com/rss/search?q=Puget+Sound+Energy+leadership+executive+director&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=Puget+Sound+Energy+appointed+promoted+resigned&hl=en-US&gl=US&ceid=US:en",
    ],
    "PSE Company News": [
        "https://news.google.com/rss/search?q=%22Puget+Sound+Energy%22+news&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=PSE+utility+Washington+rate+grid+renewable&hl=en-US&gl=US&ceid=US:en",
    ],
    "US Utilities Pulse": {
        "PacifiCorp": "https://news.google.com/rss/search?q=PacifiCorp+OR+%22Pacific+Power%22+utility&hl=en-US&gl=US&ceid=US:en",
        "Portland General": "https://news.google.com/rss/search?q=%22Portland+General+Electric%22+news&hl=en-US&gl=US&ceid=US:en",
        "Avangrid": "https://news.google.com/rss/search?q=Avangrid+utility+news&hl=en-US&gl=US&ceid=US:en",
        "NV Energy": "https://news.google.com/rss/search?q=%22NV+Energy%22+utility+news&hl=en-US&gl=US&ceid=US:en",
        "Arizona Public Service": "https://news.google.com/rss/search?q=%22Arizona+Public+Service%22+APS+utility&hl=en-US&gl=US&ceid=US:en",
        "Xcel Energy": "https://news.google.com/rss/search?q=%22Xcel+Energy%22+utility+news&hl=en-US&gl=US&ceid=US:en",
        "Evergy": "https://news.google.com/rss/search?q=Evergy+utility+news&hl=en-US&gl=US&ceid=US:en",
        "Idaho Power": "https://news.google.com/rss/search?q=%22Idaho+Power%22+utility+news&hl=en-US&gl=US&ceid=US:en",
    },
}


def fetch_articles(feed_url, lookback_days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    articles = []
    try:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries:
            try:
                pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pub = datetime.now(timezone.utc)
            if pub >= cutoff:
                articles.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:400],
                    "published": pub.strftime("%b %d, %Y"),
                    "source": entry.get("source", {}).get("title", ""),
                })
    except Exception as e:
        print(f"  WARNING: Failed to fetch {feed_url}: {e}", file=sys.stderr)
    return articles


def build_articles_text(articles):
    if not articles:
        return "No articles found in the lookback period."
    lines = []
    for a in articles[:15]:
        lines.append(f"HEADLINE: {a['title']}")
        if a.get("summary"):
            import re
            clean = re.sub(r"<[^>]+>", "", a["summary"])
            lines.append(f"SNIPPET: {clean[:300]}")
        lines.append(f"URL: {a['link']}")
        lines.append("")
    return "\n".join(lines)


def call_groq(section_name, articles_text):
    url = "https://api.groq.com/openai/v1/chat/completions"
    system_prompt = (
        "You are briefing an Accenture consulting team on Puget Sound Energy. "
        f"Write a concise HTML digest section for {section_name}. "
        "For each article include: one-sentence summary and one sentence on why it matters to a utility consulting team. "
        "Use h3 headers and p tags. No markdown. Return only HTML fragment."
    )
    payload = {
        "model": CONFIG["groq_model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": articles_text},
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {CONFIG['groq_api_key']}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  WARNING: Groq call failed for {section_name}: {e}", file=sys.stderr)
        return "<p><em>Summary unavailable — check Groq API key and quota.</em></p>"


def build_html_email(sections_html, leadership_data, changes):
    teal = "#0f6e56"
    now = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # Leadership changes alert block
    changes_block = ""
    if changes:
        items = ""
        for c in changes:
            if c["type"] == "added":
                items += f"<li><strong style='color:#16a34a'>ADDED</strong>: {c['name']} — {c.get('new_title', '')}</li>"
            elif c["type"] == "removed":
                items += f"<li><strong style='color:#dc2626'>REMOVED</strong>: {c['name']} — was {c.get('old_title', '')}</li>"
            else:
                items += (
                    f"<li><strong style='color:#d97706'>CHANGED</strong>: {c['name']} — "
                    f"{c.get('old_title', '')} &rarr; {c.get('new_title', '')}</li>"
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
          <div style="font-size:14px;line-height:1.6;color:#374151;">{content}</div>
        </div>"""

    # Leadership table
    leadership_table = ""
    if leadership_data:
        rows = "".join(
            f"<tr><td style='padding:9px 14px;border-bottom:1px solid #e5e8ec;font-weight:600;color:#0a0f1e'>{name}</td>"
            f"<td style='padding:9px 14px;border-bottom:1px solid #e5e8ec;color:#6b7280'>{info['title']}</td>"
            f"<td style='padding:9px 14px;border-bottom:1px solid #e5e8ec;color:#9ca3af;font-size:11px;text-transform:uppercase;letter-spacing:.05em'>{info['source']}</td></tr>"
            for name, info in leadership_data.get("people", {}).items()
        )
        leadership_table = f"""
        <div style="margin-bottom:32px;">
          <h2 style="color:{teal};border-bottom:2px solid {teal};padding-bottom:6px;margin:0 0 16px;font-size:17px;">Current PSE Leadership</h2>
          <table style="width:100%;border-collapse:collapse;font-size:13px;background:#fff;border:1px solid #e5e8ec;border-radius:4px;overflow:hidden;">
            <thead>
              <tr style="background:#f8f9fb;">
                <th style="padding:9px 14px;text-align:left;font-weight:600;color:#374151">Name</th>
                <th style="padding:9px 14px;text-align:left;font-weight:600;color:#374151">Title</th>
                <th style="padding:9px 14px;text-align:left;font-weight:600;color:#374151">Role</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:Arial,Helvetica,sans-serif;background:#f8f9fb;margin:0;padding:24px 0;">
  <div style="max-width:680px;margin:0 auto;background:#fff;border:1px solid #e5e8ec;border-radius:8px;overflow:hidden;">
    <div style="background:{teal};padding:28px 32px;">
      <h1 style="color:#fff;margin:0 0 4px;font-size:22px;font-weight:700;letter-spacing:-.3px;">PSE Intel — Weekly Digest</h1>
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
    print(f"  Email sent to: {', '.join(CONFIG['recipients'])}")


def main():
    print("PSE Intel — Weekly Digest")

    if not CONFIG["groq_api_key"]:
        print("ERROR: GROQ_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    if not CONFIG["smtp_user"] or not CONFIG["smtp_password"]:
        print("ERROR: GMAIL_EMAIL / GMAIL_APP_PASSWORD environment variables not set", file=sys.stderr)
        sys.exit(1)

    # Load leadership snapshot
    leadership_data = None
    changes = []
    snapshot_path = Path("leadership_snapshot.json")
    if snapshot_path.exists():
        with open(snapshot_path, encoding="utf-8") as f:
            leadership_data = json.load(f)
        changes = leadership_data.get("changes", [])
        print(f"  Loaded snapshot: {len(leadership_data.get('people', {}))} people, {len(changes)} changes")
    else:
        print("  No leadership_snapshot.json found — run monitor.py first")

    # Fetch and summarize each section
    sections_html = {}
    lookback = CONFIG["lookback_days"]

    for section_name, feeds in FEEDS.items():
        print(f"\n  [{section_name}]")
        articles = []
        if isinstance(feeds, dict):
            for utility, url in feeds.items():
                feed_articles = fetch_articles(url, lookback)
                for a in feed_articles:
                    a["utility"] = utility
                articles.extend(feed_articles)
                print(f"    {utility}: {len(feed_articles)} articles")
        else:
            for url in feeds:
                batch = fetch_articles(url, lookback)
                articles.extend(batch)
            print(f"    {len(articles)} articles total")

        articles_text = build_articles_text(articles)
        print(f"    Calling Groq ({CONFIG['groq_model']})...")
        sections_html[section_name] = call_groq(section_name, articles_text)

    # Build and send
    subject = f"PSE Intel Weekly Digest — {datetime.now(timezone.utc).strftime('%B %d, %Y')}"
    if changes:
        subject = f"[ALERT] {subject}"

    html = build_html_email(sections_html, leadership_data, changes)
    print(f"\n  Sending: {subject}")
    send_email(html, subject)
    print("\nDone.")


if __name__ == "__main__":
    main()
