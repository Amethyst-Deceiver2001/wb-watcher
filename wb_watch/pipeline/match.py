"""Re-derive item_mentions from already-stored Telegram posts.

scan_telegram writes mentions inline as it scrapes. This module re-runs the matching
over posts already in the DB — useful to backfill after adding items, or to rebuild
the mention graph without hitting Telegram again. Idempotent (mentions have a UNIQUE
constraint).
"""
from __future__ import annotations

import json
import sqlite3

from .. import db
from ..tg import extract


def rematch(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT id, channel_id, text FROM tg_posts").fetchall()
    written = 0
    for row in rows:
        intel = extract.extract(row["text"] or "")
        for nm_id in intel["wb_nm_ids"]:
            db.add_mention(conn, {
                "nm_id": nm_id,
                "marketplace": "wb",
                "external_id": str(nm_id),
                "tg_post_id": row["id"],
                "channel_id": row["channel_id"],
                "matched_url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
            })
            written += 1
        for oz in intel["ozon_ids"]:
            db.add_mention(conn, {
                "nm_id": None,
                "marketplace": "ozon",
                "external_id": oz,
                "tg_post_id": row["id"],
                "channel_id": row["channel_id"],
                "matched_url": f"https://www.ozon.ru/product/{oz}",
            })
            written += 1
    return written
