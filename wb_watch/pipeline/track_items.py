"""Snapshot one or all tracked items: card + details + reviews + seller.

Upserts item identity, appends a price/stock/rating snapshot, stores new reviews, and
refreshes the seller's legal identity. Safe to run repeatedly — that's how the time
series and the review archive accumulate.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from rich.console import Console

from .. import db
from ..analysis import ocr
from ..wb import card, details, images, reviews, seller

console = Console()

_NM_RE = re.compile(r"catalog/(\d+)")
_SELLER_RE = re.compile(r"seller/(\d+)")


def parse_seed_line(line: str) -> int | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = _NM_RE.search(line)
    if m:
        return int(m.group(1))
    if line.isdigit():
        return int(line)
    return None


def parse_seed_seller_line(line: str) -> int | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = _SELLER_RE.search(line)
    if m:
        return int(m.group(1))
    if line.isdigit():
        return int(line)
    return None


def track_item(conn: sqlite3.Connection, nm_id: int, source: str = "seed",
               discovery_query: str | None = None) -> dict[str, Any]:
    """Snapshot one item. Resilient to persistent 429s on any sub-call: the card
    fetch is required (no card, nothing to store), but details/seller/reviews each
    degrade independently — a rate-limited reviews host shouldn't lose the price
    snapshot that already succeeded, and vice versa. Never raises; callers doing a
    long unattended sweep (discover, track --all, tg-scan) rely on that.
    """
    result = {"nm_id": nm_id, "ok": False, "reviews_added": 0}
    try:
        c = card.fetch_card(nm_id)
    except RuntimeError as exc:
        console.print(f"[red]nm {nm_id}: card fetch failed ({exc})[/]")
        return result
    if not c:
        console.print(f"[yellow]nm {nm_id}: no card data (delisted?)[/]")
        db.mark_delisted(conn, nm_id, source=source, discovery_query=discovery_query)
        return result

    item = c["item"]
    try:
        d = details.fetch_details(nm_id) or {}
    except RuntimeError as exc:
        console.print(f"[yellow]nm {nm_id}: details unavailable ({exc})[/]")
        d = {}
    merged = {
        **item,
        "description": d.get("description"),
        "characteristics": d.get("characteristics"),
        "certificate": d.get("certificate"),
        "vendor_code": d.get("vendor_code"),
        "subj_name": d.get("subj_name"),
        "wb_create_date": d.get("wb_create_date"),
        "imt_id": item.get("imt_id") or d.get("imt_id"),
        "source": source,
        "discovery_query": discovery_query,
    }
    db.upsert_item(conn, merged)
    db.add_snapshot(conn, c["snapshot"])

    # Seller identity.
    sid = item.get("supplier_id")
    if sid:
        try:
            s = seller.fetch_seller(sid)
        except RuntimeError as exc:
            console.print(f"[yellow]nm {nm_id}: seller lookup failed ({exc})[/]")
            s = None
        if s:
            s["supplier_rating"] = item.get("supplier_rating")
            db.upsert_seller(conn, s)

    # Reviews (keyed on imt_id).
    imt = merged["imt_id"]
    if imt:
        try:
            rv = reviews.fetch_reviews(nm_id, imt)
        except RuntimeError as exc:
            console.print(f"[yellow]nm {nm_id}: reviews unavailable ({exc})[/]")
            rv = None
        if rv:
            result["reviews_added"] = db.add_reviews(conn, rv["reviews"])

    # Gallery images (OCR for baked-in promotional text — see analysis/ocr.py).
    # Best-effort like everything else above: a slow/unreachable basket host
    # shouldn't lose the price/review data already captured this call.
    try:
        result["images_signal"] = analyze_item_images(conn, nm_id, item.get("pics") or 0)
    except Exception as exc:  # noqa: BLE001 - never let image OCR fail a track
        console.print(f"[yellow]nm {nm_id}: image analysis failed ({exc})[/]")
        result["images_signal"] = 0

    result["ok"] = True
    img_suffix = (
        f" | +{result['images_signal']} image signal" if result["images_signal"] else ""
    )
    console.print(
        f"[green]nm {nm_id}[/] {item.get('name', '')[:48]} "
        f"| +{result['reviews_added']} reviews{img_suffix}"
    )
    return result


def analyze_item_images(conn: sqlite3.Connection, nm_id: int, pics: int) -> int:
    """OCR up to a few gallery images for one item and store results.
    Returns the count of images whose OCR text matched a combat-context
    phrase (analysis/signals.py) — the same "at least one hit" reporting
    style as reviews_added."""
    if pics <= 0:
        return 0
    gallery = images.fetch_gallery(nm_id, pics)
    results = []
    signal_count = 0
    for idx, data in gallery:
        analysis = ocr.analyze_image(data)
        if analysis["field_use_phrase"]:
            signal_count += 1
        results.append({
            "image_index": idx,
            "url": images.image_url(nm_id, idx, images.basket.resolve_host(nm_id)),
            **analysis,
        })
    if results:
        db.add_image_analysis(conn, nm_id, results)
    return signal_count


def track_all(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for nm_id in db.tracked_nm_ids(conn):
        # Preserve original source/query on refresh (upsert only sets them on insert).
        out.append(track_item(conn, nm_id, source="seed"))
    return out


# How long a confirmed-zero-photos result (images_checked_at) is trusted
# before re-checking — WB does occasionally add photos to a previously bare
# size/color variant, but rarely enough that re-fetching its card every
# single scan-images run (the prior behavior) was pure waste: one run found
# 7,324 "processed" items added only 11 real image sets, because item_images
# never gets a row (and so never trips the existing-count skip below) when
# a card genuinely has pics=0.
_ZERO_PHOTOS_RECHECK_DAYS = 14


def _is_stale(ts: str | None) -> bool:
    if not ts:
        return True
    checked = datetime.fromisoformat(ts)
    return datetime.now(timezone.utc) - checked > timedelta(days=_ZERO_PHOTOS_RECHECK_DAYS)


def scan_images(conn: sqlite3.Connection, limit: int | None = None) -> dict[str, int]:
    """Backfill image OCR over already-tracked items that don't have it yet,
    or that were scanned under the old max_images=6 cap and have more
    gallery images available now (bumped to
    images.DEFAULT_MAX_IMAGES=12 — see wb/images.py).

    Skip is keyed on stored-count vs. pics, not bare existence: 6,257 of the
    8,687 items already scanned before the cap bump have exactly 6 rows
    stored, and a bare-existence check would skip them forever, silently
    losing the whole point of the bump. add_image_analysis is INSERT OR
    REPLACE per (nm_id, image_index), so re-scanning is idempotent/cheap —
    it only ever adds the missing higher-index rows.
    """
    processed = signals = skipped = 0
    for nm_id in db.tracked_nm_ids(conn):
        existing = db.image_analysis_count(conn, nm_id)
        if existing >= images.DEFAULT_MAX_IMAGES:
            skipped += 1
            continue
        if existing == 0 and not _is_stale(db.images_checked_at(conn, nm_id)):
            skipped += 1
            continue
        try:
            c = card.fetch_card(nm_id)
        except RuntimeError as exc:
            console.print(f"[yellow]nm {nm_id}: card fetch failed ({exc})[/]")
            continue
        if not c:
            continue
        pics = c["item"].get("pics") or 0
        if existing and existing >= min(pics, images.DEFAULT_MAX_IMAGES):
            skipped += 1
            continue
        signals += analyze_item_images(conn, nm_id, pics)
        if pics <= 0:
            db.mark_images_checked(conn, nm_id)
        processed += 1
        if processed % 50 == 0:
            console.print(f"[cyan]scan-images:[/] {processed} processed so far")
        if limit and processed >= limit:
            break
    return {"processed": processed, "signals": signals, "skipped": skipped}
