"""Refresh the docs/index.html news ticker end-to-end: run news-scan, then
splice a freshly rendered ticker fragment into the page.

Usage: wb-watch update-news-ticker [--top N] [--no-scan]

Picks the top N most recent stored headlines, capped per outlet per run so a
single fast-moving outlet (e.g. Kommersant during a breaking story) can't
crowd out the others, then replaces the <div class="newsbox ui">...</div>
block in docs/index.html in place.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import re
from pathlib import Path

from .. import db
from ..pipeline import news_scan

INDEX_HTML = Path(__file__).resolve().parents[2] / "docs" / "index.html"

# class list matched loosely ([^"]*) rather than a fixed string: the live
# markup is currently `newsbox newsbox-big ui` (drifted from an earlier
# `newsbox ui` during a styling pass), and a literal-string regex silently
# fails to match at all if that class list changes again — found live when
# this script raised "block not found" against the actual current file.
_BLOCK_RE = re.compile(
    r'    <div class="newsbox[^"]*">.*?\n    </div>', re.DOTALL
)

_MONTHS = ["янв", "февр", "марта", "апр", "мая", "июня", "июля",
           "авг", "сент", "окт", "нояб", "дек"]


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


def _render_from_rows(rows: list) -> str:
    items = []
    for r in rows:
        date_str = ""
        if r["published"]:
            try:
                dt = _dt.datetime.fromisoformat(r["published"])
                date_str = f"{dt.day} {_MONTHS[dt.month - 1]} {dt.year}"
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
    # Indentation/class list/label text kept identical to the file's own
    # current markup (see _BLOCK_RE) so a diff only ever shows the <li> rows
    # changing, not incidental reformatting.
    return (
        '    <div class="newsbox newsbox-big ui">\n'
        '      <div class="newsbox-label">Wildberries и Татьяна Ким в прессе '
        '— прямо сейчас</div>\n'
        '      <ul class="newsbox-list">\n'
        f'{rows_html}\n'
        '      </ul>\n'
        '    </div>'
    )


def run(top: int = 6, do_scan: bool = True) -> dict:
    added = news_scan.run() if do_scan else 0

    conn = db.connect()
    db.init_db(conn)  # in case do_scan=False was passed against a fresh DB
    rows = db.recent_news_items(conn, limit=max(top * 4, 24))
    picked = _pick_diverse(rows, top)
    fragment = _render_from_rows(picked)

    html_text = INDEX_HTML.read_text(encoding="utf-8")
    if not _BLOCK_RE.search(html_text):
        raise RuntimeError("news ticker block not found in docs/index.html")
    # re.sub treats backslashes in the replacement specially (\1, \g<0>, ...);
    # the fragment is plain text, so escape any literal backslash first.
    new_html = _BLOCK_RE.sub(
        fragment.replace("\\", "\\\\"), html_text, count=1
    )
    INDEX_HTML.write_text(new_html, encoding="utf-8")

    return {"scanned_new": added, "ticker_items": len(picked)}
