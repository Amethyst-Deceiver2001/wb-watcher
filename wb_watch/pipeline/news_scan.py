"""Harvest fresh Wildberries/Tatyana Kim headlines from RSS feeds of a fixed
set of Russian-language outlets, for the "in the press" ticker on the docs
site (see site/build_assets.py's build_news_ticker_html).

Static curated feed list, same reasoning as the review ticker: a handful of
outlets we trust and want editorial control over, fetched fresh each run
rather than pulled from a live client-side widget (the docs site is static).
Meant to be re-run periodically (cron or by hand) to keep the ticker current;
per project convention this is a network job and is run by the user, not
kicked off automatically from a conversation.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .. import db, http

# name -> RSS feed URL. Kept short and outlet-labelled so the ticker can show
# provenance. iStories and Moscow Times don't expose a feed that a non-browser
# client can fetch (403/DNS failures as of 2026-07-30) — left out rather than
# silently retried forever; add back if/when they open one up.
_FEEDS: dict[str, str] = {
    "Meduza": "https://meduza.io/rss/all",
    "Медиазона": "https://zona.media/rss/all",
    "Коммерсантъ": "https://www.kommersant.ru/rss/news.xml",
    "РБК": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
}

# Match the company or its owner by name; deliberately NOT bare "ким" (too
# common a surname/DPRK-leader collision) — only "татьяна ким" counts unless
# wildberries/вайлдберриз is also present.
_WB_RE = re.compile(r"wildberries|вайлдберриз|вб\b.{0,20}маркетплейс", re.I)
_KIM_RE = re.compile(r"татьян\w*\s+ким", re.I)


def _matches(title: str, summary: str) -> bool:
    text = f"{title} {summary}"
    return bool(_WB_RE.search(text) or _KIM_RE.search(text))


def _parse_rfc822(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _fetch_feed(url: str) -> list[dict[str, str | None]]:
    resp = http._request(url, params=None)  # raw XML, not JSON
    if resp.status_code != 200:
        return []
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        summary = (item.findtext("description") or "").strip()
        pub = item.findtext("pubDate")
        if title and link:
            out.append({"title": title, "url": link, "summary": summary,
                        "published": _parse_rfc822(pub)})
    return out


def run() -> int:
    """Fetch all configured feeds, filter for WB/Kim relevance, store new
    matches. Returns count of newly stored headlines."""
    conn = db.connect()
    db.init_db(conn)  # news_items may not exist yet on a fresh DB (e.g. CI)
    added = 0
    for outlet, feed_url in _FEEDS.items():
        try:
            entries = _fetch_feed(feed_url)
        except Exception:
            continue
        matched = [
            {"outlet": outlet, "title": e["title"], "url": e["url"],
             "published": e["published"]}
            for e in entries if _matches(e["title"], e["summary"] or "")
        ]
        if matched:
            added += db.add_news_items(conn, matched)
    return added
