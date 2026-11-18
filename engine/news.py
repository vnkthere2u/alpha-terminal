"""Free headline feed: Google News RSS topic searches + central-bank press feeds.

No API key, no quota. This is the baseline news input; Gemini's Google Search
grounding (when the free tier allows it) adds detail on top.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (compatible; alpha-terminal/1.0; +macro research)"}

TOPICS = [
    "Federal Reserve OR FOMC OR Powell rates",
    "ECB OR \"Bank of Japan\" OR PBOC interest rate decision",
    "RBI rupee OR \"FPI flows\" OR \"FII selling\" OR \"FII buying\"",
    "Treasury yields OR \"bond auction\" OR \"term premium\"",
    "CPI OR payrolls OR PMI OR GDP \"than expected\"",
    "oil prices OPEC OR \"crude inventories\" OR Brent",
    "gold prices \"central bank\" OR ETF OR record",
    "copper OR silver OR \"natural gas\" prices supply",
    "tariffs OR sanctions markets",
    "China yuan OR stimulus OR PBOC economy",
    "\"high yield\" OR \"credit spreads\" OR \"private credit\" stress",
    "yen intervention OR \"carry trade\"",
    "UAE OR Gulf OR Saudi economy markets",
]

CB_FEEDS = {
    "Fed": "https://www.federalreserve.gov/feeds/press_all.xml",
    "ECB": "https://www.ecb.europa.eu/rss/press.html",
    "RBI": "https://www.rbi.org.in/pressreleases_rss.xml",
    "BoJ": "https://www.boj.or.jp/en/rss/whatsnew.xml",
}


def _gnews_url(q: str, days: int) -> str:
    return (f"https://news.google.com/rss/search?q={quote_plus(q + f' when:{days}d')}"
            "&hl=en-US&gl=US&ceid=US:en")


def _parse(xml_text: str, source_hint: str) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for item in root.iter():
        if not item.tag.lower().endswith("item") and not item.tag.lower().endswith("entry"):
            continue
        fields = {c.tag.split("}")[-1].lower(): (c.text or "").strip() for c in item}
        title = fields.get("title", "")
        date_s = fields.get("pubdate") or fields.get("date") or fields.get("updated") or fields.get("published")
        try:
            dt = parsedate_to_datetime(date_s) if date_s and not date_s[:4].isdigit() else datetime.fromisoformat(date_s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = None
        src = fields.get("source") or source_hint
        if title:
            out.append({"title": re.sub(r"\s+", " ", title), "source": src, "date": dt})
    return out


def headlines(days: int = 2, per_topic: int = 6, max_items: int = 70) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items: list[dict] = []
    for q in TOPICS:
        try:
            r = requests.get(_gnews_url(q, days), headers=UA, timeout=20)
            if r.ok:
                items += _parse(r.text, "Google News")[:per_topic]
        except Exception as e:
            log.warning("gnews %s: %s", q[:30], e)
    for name, url in CB_FEEDS.items():
        try:
            r = requests.get(url, headers=UA, timeout=20)
            if r.ok:
                items += [dict(i, source=name) for i in _parse(r.text, name)[:8]]
        except Exception as e:
            log.warning("feed %s: %s", name, e)
    seen, out = set(), []
    for i in sorted(items, key=lambda x: x["date"] or cutoff, reverse=True):
        if i["date"] and i["date"] < cutoff:
            continue
        key = re.sub(r"[^a-z0-9]", "", i["title"].lower().split(" - ")[0])[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return out[:max_items]


def as_text(items: list[dict]) -> str:
    return "\n".join(f"[{i['date'].strftime('%d %b %H:%M') if i['date'] else '?'}] {i['title']} ({i['source']})"
                     for i in items)
