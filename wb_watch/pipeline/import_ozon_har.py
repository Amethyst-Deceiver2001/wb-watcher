"""Ingest a user-captured Ozon HAR export into the DB.

Unlike the rest of pipeline/ (discover.py, track_items.py, resolve_ozon.py),
this makes zero network calls — it only reads a HAR file already sitting on
disk, produced by the user's own browser (DevTools > Network > Export HAR).
Ozon's product/listing/review APIs are bot-walled for plain HTTP clients (see
ozon/har_parse.py's module docstring), so a live equivalent of wb/card.py
isn't possible; capturing the HAR *is* the crawling step, and it's the user's
to run, same as every other Ozon reconnaissance this project has done. Parsing
the file afterwards is offline and safe to run directly.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from rich.console import Console

from .. import db
from ..ozon import har_parse

console = Console()


def import_har(conn: sqlite3.Connection, path: str | Path) -> dict[str, int]:
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        har = json.load(fh)
    source = path.name

    counts = {
        "product_pages": 0,
        "listing_items": 0,
        "detail_updates": 0,
        "reviews": 0,
        "skipped_no_body": 0,
    }

    for entry in har.get("log", {}).get("entries", []):
        url = entry["request"]["url"]
        content = entry["response"].get("content", {})
        text = content.get("text")
        if text is None:
            counts["skipped_no_body"] += 1
            continue

        kind = har_parse.entry_kind(url)
        if kind is None:
            continue

        if kind == "product_html":
            if 'data-widget="webProductHeading"' not in text and "og:title" not in text:
                continue
            detail = har_parse.parse_product_page(text)
            product_id = har_parse.product_id_from_url(
                detail.get("og_url") or url
            )
            if not product_id:
                continue
            db.upsert_ozon_item_detail(conn, product_id, detail, source)
            counts["product_pages"] += 1
            continue

        if content.get("mimeType") != "application/json":
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if "widgetStates" not in payload:
            continue

        if kind == "reviews":
            reviews = har_parse.parse_reviews(payload)
            for rv in reviews:
                db.add_ozon_review(conn, rv)
            counts["reviews"] += len(reviews)
            continue

        # listing_or_product_json: try both — a given response is one or the
        # other in practice, but nothing stops trying both cheaply.
        tiles = har_parse.parse_listing_grid(payload)
        for tile in tiles:
            db.upsert_ozon_listing_item(conn, tile, source)
        counts["listing_items"] += len(tiles)

        extra = har_parse.parse_characteristics_and_description(payload)
        if extra.get("characteristics") or extra.get("description_text"):
            product_id = har_parse.product_id_from_url(url)
            if product_id:
                db.upsert_ozon_item_detail(conn, product_id, extra, source)
                counts["detail_updates"] += 1

    console.print(
        f"[green]import-ozon-har:[/] {source} -> "
        f"{counts['product_pages']} product pages, "
        f"{counts['listing_items']} listing tiles, "
        f"{counts['detail_updates']} detail updates, "
        f"{counts['reviews']} reviews "
        f"({counts['skipped_no_body']} entries had no captured body)"
    )
    return counts
