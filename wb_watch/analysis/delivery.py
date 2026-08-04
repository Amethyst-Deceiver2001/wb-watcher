"""Infer whether an item ships from a shared WB fulfillment network warehouse
("со склада WB") or from a warehouse exclusive to its own seller ("со склада
продавца").

WB's card API doesn't label this directly — the product page shows it as plain
text ("Доставка со склада продавца"/"со склада Wildberries"), but that page is
bot-walled (498s) so it can't be scraped to confirm per-item. What the card API
*does* give us is a `wh` (warehouse) id per stock line. Cross-referencing `wh`
against every other tracked item's latest snapshot shows most `wh` ids are
shared by dozens to hundreds of distinct sellers — those are WB's own central
fulfillment centers (e.g. wh=507, used by 91 different suppliers in this DB;
that's WB's Коледино hub, not any one seller's private warehouse). A `wh` id
that only ever shows up under one supplier is the seller's own point, i.e.
self-fulfillment.

This is a corpus-relative heuristic, not ground truth from WB — confidence
scales with how many distinct sellers we've observed at that `wh` id, which is
itself a function of how much of the catalog we've tracked. Treat the
"likely" labels as directional, not certain.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

_SHARED_MIN = 5  # other distinct sellers at a wh id -> call it a WB network hub


def build_wh_supplier_counts(conn: sqlite3.Connection) -> dict[int, set[int]]:
    """wh id -> set of distinct supplier_ids seen stocking there (latest
    snapshot per item only, so stale stock history doesn't inflate counts)."""
    rows = conn.execute(
        """SELECT snap.stocks, i.supplier_id
           FROM item_snapshots snap
           JOIN items i ON i.nm_id = snap.nm_id
           WHERE snap.id IN (SELECT MAX(id) FROM item_snapshots GROUP BY nm_id)
             AND i.supplier_id IS NOT NULL"""
    ).fetchall()
    counts: dict[int, set[int]] = defaultdict(set)
    for r in rows:
        if not r["stocks"]:
            continue
        for st in json.loads(r["stocks"]):
            wh = st.get("wh")
            if wh is not None:
                counts[wh].add(r["supplier_id"])
    return counts


def classify_delivery(
    stocks: list[dict[str, Any]] | None,
    supplier_id: int | None,
    wh_supplier_counts: dict[int, set[int]],
) -> str:
    """Classify one item's current stock lines given the corpus-wide wh map."""
    if not stocks:
        return "unknown"
    labels = set()
    for st in stocks:
        wh = st.get("wh")
        if wh is None:
            continue
        others = wh_supplier_counts.get(wh, set()) - {supplier_id}
        if len(others) >= _SHARED_MIN:
            labels.add("wb_network")
        elif len(others) == 0:
            labels.add("seller_warehouse_likely")
        else:
            labels.add("ambiguous")
    if not labels:
        return "unknown"
    if len(labels) > 1:
        return "mixed"
    return labels.pop()
