"""Harvest fresh Wildberries/Tatyana Kim headlines from RSS feeds, for the
"in the press" tickers on the docs site (docs/index.html — Russian outlets —
and docs/en/index.html — English-language outlets).

Static curated feed lists, same reasoning as the review ticker: a handful of
outlets we trust and want editorial control over, fetched fresh each run
rather than pulled from a live client-side widget (the docs site is static).
Meant to be re-run periodically (cron or by hand) to keep the tickers current;
per project convention this is a network job and is run by the user (or a
scheduled CI job, since it's a lightweight public-RSS pull, not a marketplace
crawl), not kicked off ad hoc from a conversation.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .. import db, http

# name -> RSS feed URL. Kept short and outlet-labelled so the ticker can show
# provenance. iStories and Moscow Times' Russian edition don't expose a feed
# that a non-browser client can fetch (403/DNS failures as of 2026-07-30) —
# left out rather than silently retried forever; add back if/when they open
# one up.
_FEEDS_RU: dict[str, str] = {
    "Meduza": "https://meduza.io/rss/all",
    "Медиазона": "https://zona.media/rss/all",
    "Коммерсантъ": "https://www.kommersant.ru/rss/news.xml",
    "РБК": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
}

# English-language outlets for docs/en/index.html. Forbes and AP don't expose
# a fetchable general-news RSS feed (checked 2026-08-08: Forbes has no public
# search feed, AP's public RSS was discontinued) — left out rather than
# guessing at a URL. Fortune's feed URL 301-redirects to a query-string path
# (fortune.com/feed/fortune-feeds/?id=...) that's stable across requests, so
# it's used as-is (http.py follows redirects by default).
_FEEDS_EN: dict[str, str] = {
    "Kyiv Independent": "https://kyivindependent.com/news-archive/rss/",
    "The Moscow Times": "https://www.themoscowtimes.com/rss/news",
    "Euronews": "https://www.euronews.com/rss",
    "Fortune": "https://fortune.com/feed/",
}

# Match the company or its owner by name; deliberately NOT bare "ким"/"kim"
# (too common a surname/DPRK-leader collision) — only "татьяна ким"/
# "tat(i|y)ana kim" counts unless wildberries/вайлдберриз is also present.
# One matcher covers both languages: "wildberries" appears unchanged (Latin
# script) in English-language articles too.
_WB_RE = re.compile(r"wildberries|вайлдберриз|вб\b.{0,20}маркетплейс", re.I)
_KIM_RE = re.compile(r"татьян\w*\s+ким|tat[iy]ana\s+kim", re.I)


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


def _scan(feeds: dict[str, str], lang: str, conn) -> int:
    added = 0
    for outlet, feed_url in feeds.items():
        try:
            entries = _fetch_feed(feed_url)
        except Exception:
            continue
        matched = [
            {"outlet": outlet, "title": e["title"], "url": e["url"],
             "published": e["published"], "lang": lang}
            for e in entries if _matches(e["title"], e["summary"] or "")
        ]
        if matched:
            added += db.add_news_items(conn, matched)
    return added


def run() -> int:
    """Russian-outlet scan only — back-compat entry point for the plain
    `wb-watch news-scan` command. Returns count of newly stored headlines."""
    conn = db.connect()
    db.init_db(conn)  # news_items may not exist yet on a fresh DB (e.g. CI)
    return _scan(_FEEDS_RU, "ru", conn)


def run_en() -> int:
    """English-outlet scan, for docs/en/index.html's ticker."""
    conn = db.connect()
    db.init_db(conn)
    return _scan(_FEEDS_EN, "en", conn)
