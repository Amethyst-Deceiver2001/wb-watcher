"""Refresh both docs-site news tickers end-to-end: run news-scan (both the
Russian and English outlet lists), then splice a freshly rendered fragment
into each page — docs/index.html (Russian outlets) and docs/en/index.html
(English-language outlets).

Usage: wb-watch update-news-ticker [--top N] [--no-scan]

Picks the top N most recent stored headlines per page, capped per outlet per
run so a single fast-moving outlet (e.g. Kommersant during a breaking story)
can't crowd out the others, then replaces the <div class="newsbox ...">...
</div> block in place.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import re
from pathlib import Path

from .. import db
from ..pipeline import news_scan

_DOCS = Path(__file__).resolve().parents[2] / "docs"

# class list matched loosely ([^"]*) rather than a fixed string: the live
# markup is currently `newsbox newsbox-big ui` (drifted from an earlier
# `newsbox ui` during a styling pass), and a literal-string regex silently
# fails to match at all if that class list changes again — found live when
# this script raised "block not found" against the actual current file.
_BLOCK_RE = re.compile(
    r'    <div class="newsbox[^"]*">.*?\n    </div>', re.DOTALL
)

_MONTHS_RU = ["янв", "февр", "марта", "апр", "мая", "июня", "июля",
              "авг", "сент", "окт", "нояб", "дек"]
_MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]


def _date_ru(dt: _dt.datetime) -> str:
    return f"{dt.day} {_MONTHS_RU[dt.month - 1]} {dt.year}"


def _date_en(dt: _dt.datetime) -> str:
    return f"{_MONTHS_EN[dt.month - 1]} {dt.day}, {dt.year}"


# Each page's own label/date-format/scan function, kept exactly matching
# what's already live on that page so a diff only ever shows the <li> rows
# changing, never incidental reformatting or a mistranslated label.
_PAGES = [
    {
        "path": _DOCS / "index.html",
        "lang": "ru",
        "label": "Wildberries и Татьяна Ким в прессе — прямо сейчас",
        "date_fmt": _date_ru,
        "scan": news_scan.run,
    },
    {
        "path": _DOCS / "en" / "index.html",
        "lang": "en",
        "label": "Wildberries in the international press — right now",
        "date_fmt": _date_en,
        "scan": news_scan.run_en,
    },
]


def _pick_diverse(rows: list, top: int, per_outlet_cap: int = 2) -> list:
    """Most recent headlines, but cap how many any single outlet
    contributes so the ticker reads as a cross-outlet picture, not one
    outlet's firehose."""
    counts: dict[str, int] = {}
    picked = []
    for r in rows:
        if counts.get(r["outlet"], 0) >= per_outlet_cap:
            continue
        picked.append(r)
        counts[r["outlet"]] = counts.get(r["outlet"], 0) + 1
        if len(picked) >= top:
            break
    return picked


def _render_from_rows(rows: list, label: str, date_fmt) -> str:
    items = []
    for r in rows:
        date_str = ""
        if r["published"]:
            try:
                dt = _dt.datetime.fromisoformat(r["published"])
                date_str = date_fmt(dt)
            except ValueError:
                date_str = ""
        outlet = _html.escape(r["outlet"])
        url = _html.escape(r["url"])
        title = _html.escape(r["title"])
        items.append(
            f'        <li><span class="news-outlet">{outlet}</span> '
            f'<a href="{url}" target="_blank" rel="noopener">{title}</a> '
            f'<span class="news-date">{date_str}</span></li>'
        )
    rows_html = "\n".join(items)
    return (
        '    <div class="newsbox newsbox-big ui">\n'
        f'      <div class="newsbox-label">{_html.escape(label)}</div>\n'
        '      <ul class="newsbox-list">\n'
        f'{rows_html}\n'
        '      </ul>\n'
        '    </div>'
    )


def run(top: int = 6, do_scan: bool = True) -> dict:
    conn = db.connect()
    db.init_db(conn)  # in case do_scan=False was passed against a fresh DB

    results: dict = {"pages": {}}
    for page in _PAGES:
        added = page["scan"]() if do_scan else 0
        rows = db.recent_news_items(conn, limit=max(top * 4, 24), lang=page["lang"])
        picked = _pick_diverse(rows, top)
        fragment = _render_from_rows(picked, page["label"], page["date_fmt"])

        html_text = page["path"].read_text(encoding="utf-8")
        if not _BLOCK_RE.search(html_text):
            raise RuntimeError(f"news ticker block not found in {page['path']}")
        # re.sub treats backslashes in the replacement specially (\1, \g<0>, ...);
        # the fragment is plain text, so escape any literal backslash first.
        new_html = _BLOCK_RE.sub(
            fragment.replace("\\", "\\\\"), html_text, count=1
        )
        page["path"].write_text(new_html, encoding="utf-8")

        results["pages"][page["lang"]] = {
            "scanned_new": added, "ticker_items": len(picked)
        }
    return results
