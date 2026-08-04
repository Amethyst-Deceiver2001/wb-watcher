"""Retroactively apply the analysis layer (category, seller classification, review
field-use signals) to rows collected before those features existed.

Pure local recomputation over already-stored data — no network calls, so this is
safe to run directly rather than handed off as a crawl job.
"""
from __future__ import annotations

import json
import sqlite3

from rich.console import Console

from ..analysis.categorize import categorize_item
from ..analysis.delivery import build_wh_supplier_counts, classify_delivery
from ..analysis.military_class import classify_military
from ..analysis.sellers import classify_seller
from ..analysis.signals import detect_field_use, detect_field_use_image
from ..analysis.wb_policy import classify_wb_policy

console = Console()


def backfill_categories(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT nm_id, name, subj_name FROM items").fetchall()
    n = 0
    for r in rows:
        category = categorize_item(r["name"], r["subj_name"])
        conn.execute("UPDATE items SET category=? WHERE nm_id=?", (category, r["nm_id"]))
        n += 1
    conn.commit()
    return n


def backfill_military_class(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT nm_id, name, subj_name FROM items").fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        mil = classify_military(r["name"], r["subj_name"])
        conn.execute(
            "UPDATE items SET military_class=?, military_reason=? WHERE nm_id=?",
            (mil["military_class"], mil["military_reason"], r["nm_id"]),
        )
        counts[mil["military_class"]] = counts.get(mil["military_class"], 0) + 1
    conn.commit()
    console.print(f"[cyan]military class:[/] {counts}")
    return counts


def backfill_wb_policy(conn: sqlite3.Connection) -> dict[str, int]:
    """Check every item against Wildberries' own List of Prohibited Goods
    (see analysis/wb_policy.py) — run after categories/military_class are
    computed, since the rules key off both."""
    rows = conn.execute(
        "SELECT nm_id, name, category, military_class FROM items"
    ).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        res = classify_wb_policy(r["name"], r["category"], r["military_class"])
        conn.execute(
            "UPDATE items SET wb_policy_clause=?, wb_policy_reason=? WHERE nm_id=?",
            (res["wb_policy_clause"], res["wb_policy_reason"], r["nm_id"]),
        )
        clause = res["wb_policy_clause"] or "none"
        counts[clause] = counts.get(clause, 0) + 1
    conn.commit()
    console.print(f"[cyan]wb policy clauses:[/] {counts}")
    return counts


def backfill_sellers(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT supplier_id, inn, name, ogrnip FROM sellers").fetchall()
    n = 0
    for r in rows:
        c = classify_seller(r["inn"], r["name"], r["ogrnip"])
        conn.execute(
            "UPDATE sellers SET entity_type=?, origin=? WHERE supplier_id=?",
            (c["entity_type"], c["origin"], r["supplier_id"]),
        )
        n += 1
    conn.commit()
    return n


def backfill_delivery_source(conn: sqlite3.Connection) -> dict[str, int]:
    wh_counts = build_wh_supplier_counts(conn)
    rows = conn.execute(
        """SELECT i.nm_id, i.supplier_id, snap.stocks
           FROM items i
           LEFT JOIN item_snapshots snap ON snap.id = (
               SELECT id FROM item_snapshots WHERE nm_id = i.nm_id
               ORDER BY ts DESC LIMIT 1)"""
    ).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        stocks = json.loads(r["stocks"]) if r["stocks"] else None
        label = classify_delivery(stocks, r["supplier_id"], wh_counts)
        conn.execute(
            "UPDATE items SET delivery_source=? WHERE nm_id=?", (label, r["nm_id"])
        )
        counts[label] = counts.get(label, 0) + 1
    conn.commit()
    console.print(f"[cyan]delivery source:[/] {counts}")
    return counts


def backfill_review_signals(conn: sqlite3.Connection) -> int:
    # WB splits a review into pros/cons/comment — many buyers put the whole
    # confirmation in pros alone ("Заказ на СВО") and leave the comment
    # empty, so scanning text only silently missed these (found via a
    # body-bag listing whose only two textual reviews were pros-only).
    rows = conn.execute("SELECT id, text, pros, cons FROM reviews").fetchall()
    n = 0
    flagged = 0
    for r in rows:
        combined = " ".join(filter(None, [r["text"], r["pros"], r["cons"]]))
        phrase = detect_field_use(combined)
        conn.execute(
            "UPDATE reviews SET field_use_signal=?, field_use_phrase=? WHERE id=?",
            (1 if phrase else 0, phrase, r["id"]),
        )
        n += 1
        if phrase:
            flagged += 1
    conn.commit()
    console.print(f"[cyan]review signals:[/] {flagged}/{n} flagged")
    return flagged


def backfill_description_signals(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT nm_id, description FROM items").fetchall()
    n = 0
    flagged = 0
    for r in rows:
        phrase = detect_field_use(r["description"])
        conn.execute(
            "UPDATE items SET description_signal=?, description_phrase=? WHERE nm_id=?",
            (1 if phrase else 0, phrase, r["nm_id"]),
        )
        n += 1
        if phrase:
            flagged += 1
    conn.commit()
    console.print(f"[cyan]description signals:[/] {flagged}/{n} flagged")
    return flagged


def backfill_image_signals(conn: sqlite3.Connection) -> int:
    """Re-run detect_field_use_image against already-stored OCR text — this
    was never retroactively applied when signals.py's regex changed (only
    ever set once, at scan-images time), the same kind of pipeline gap as
    wb_policy_clause used to have. Purely local: the OCR text is already in
    the DB, no image re-fetch/re-OCR needed."""
    rows = conn.execute(
        "SELECT nm_id, image_index, ocr_text FROM item_images WHERE ocr_text IS NOT NULL"
    ).fetchall()
    n = 0
    flagged = 0
    for r in rows:
        phrase = detect_field_use_image(r["ocr_text"])
        conn.execute(
            "UPDATE item_images SET field_use_signal=?, field_use_phrase=? "
            "WHERE nm_id=? AND image_index=?",
            (1 if phrase else 0, phrase, r["nm_id"], r["image_index"]),
        )
        n += 1
        if phrase:
            flagged += 1
    conn.commit()
    console.print(f"[cyan]image signals:[/] {flagged}/{n} flagged")
    return flagged


def run(conn: sqlite3.Connection) -> dict[str, int]:
    cats = backfill_categories(conn)
    mil = backfill_military_class(conn)
    wb_policy = backfill_wb_policy(conn)
    delivery = backfill_delivery_source(conn)
    sellers = backfill_sellers(conn)
    signals = backfill_review_signals(conn)
    desc_signals = backfill_description_signals(conn)
    image_signals = backfill_image_signals(conn)
    return {
        "items": cats,
        "military_class": mil,
        "wb_policy": wb_policy,
        "delivery_source": delivery,
        "sellers": sellers,
        "field_use_reviews": signals,
        "description_signals": desc_signals,
        "image_signals": image_signals,
    }
