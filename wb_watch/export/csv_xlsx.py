"""Export the DB to CSV files or a single multi-sheet XLSX workbook.

Exports both raw tables and analyst-friendly joined views:
  - items_latest   : each item with its most recent snapshot + seller identity
  - mentions_view  : each Telegram mention joined to item + channel context
  - network_view   : the extended channel network (forward + text-reference graph)
    for follow-up, with a flag for whichever are already in the watchlist
  - forwards_view  : every scraped post that is itself a forward, with its source
  - field_use_reviews : reviews with an explicit front-line/combat-use confirmation
  - seller_concentration : sellers ranked by how many tracked items they're behind,
    with entity type (legal/sole-trader/Chinese cross-border) and INN
  - category_summary : demand (Telegram mentions/channels) vs supply (WB listings/
    sellers/reviews) cross-cut by item category
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from openpyxl import Workbook

from .. import config
from ..analysis import patterns as patterns_mod

# name -> SQL. Order matters only for readability.
QUERIES: dict[str, str] = {
    "items_latest": """
        SELECT i.nm_id, i.name, i.category, i.brand, i.subj_name, i.source,
               i.discovery_query, i.delivery_source,
               s.name AS seller, s.inn, s.ogrnip, s.trademark, s.entity_type,
               s.origin AS seller_origin,
               snap.price_product/100.0 AS price_rub,
               snap.total_qty, snap.feedback_count, snap.review_rating,
               i.wb_create_date, i.first_seen, i.last_seen,
               'https://www.wildberries.ru/catalog/' || i.nm_id || '/detail.aspx' AS url
        FROM items i
        LEFT JOIN sellers s ON s.supplier_id = i.supplier_id
        LEFT JOIN item_snapshots snap ON snap.id = (
            SELECT id FROM item_snapshots WHERE nm_id = i.nm_id
            ORDER BY ts DESC LIMIT 1)
        ORDER BY i.nm_id
    """,
    # One row per distinct product (imt_id), not per color/size variant nm_id
    # — items_latest lists variants as separate rows, which inflates apparent
    # product counts in any report built off it. representative_nm_id is the
    # lowest nm_id in the group, used to build a canonical URL.
    "products_deduped": """
        SELECT i.imt_id, MIN(i.nm_id) AS representative_nm_id,
               COUNT(*) AS n_variants,
               GROUP_CONCAT(DISTINCT i.name) AS names,
               GROUP_CONCAT(DISTINCT i.category) AS categories,
               GROUP_CONCAT(DISTINCT i.military_class) AS military_classes,
               GROUP_CONCAT(DISTINCT i.brand) AS brands,
               'https://www.wildberries.ru/catalog/' || MIN(i.nm_id) ||
                   '/detail.aspx' AS representative_url
        FROM items i
        WHERE i.imt_id IS NOT NULL
        GROUP BY i.imt_id
        ORDER BY n_variants DESC
    """,
    "item_snapshots": "SELECT * FROM item_snapshots ORDER BY nm_id, ts",
    "sellers": "SELECT * FROM sellers ORDER BY supplier_id",
    # Joined on imt_id, not nm_id: WB reviews are attached to whichever
    # color/size variant nm_id the reviewer actually bought, which is very
    # often a sibling SKU under the same product (imt_id) rather than the
    # exact nm_id we tracked — joining on nm_id alone silently drops ~61% of
    # all reviews for tracked products. Also excludes reviews for items that
    # hit neither taxonomy axis (category='other' AND military_class='other')
    # — e.g. baby diapers, infant formula, shampoo, pens pulled in via the
    # similar-items expansion graph with zero connection to anything
    # war-relevant. Items still flagged 'uncertain' (genuinely ambiguous,
    # pending human review) are kept, not treated as noise.
    "reviews": """
        SELECT DISTINCT r.* FROM reviews r
        JOIN items i ON i.imt_id = r.imt_id
        WHERE NOT (i.category = 'other' AND i.military_class = 'other')
        ORDER BY r.nm_id, r.created_date
    """,
    "tg_channels": "SELECT * FROM tg_channels ORDER BY id",
    "tg_posts": "SELECT * FROM tg_posts ORDER BY channel_id, msg_id",
    "mentions_view": """
        SELECT m.marketplace, m.external_id, m.nm_id, i.name AS item_name,
               c.title AS channel, c.username AS channel_username,
               p.date AS post_date, p.views, m.matched_url, m.tg_post_id
        FROM item_mentions m
        LEFT JOIN items i ON i.nm_id = m.nm_id
        LEFT JOIN tg_posts p ON p.id = m.tg_post_id
        LEFT JOIN tg_channels c ON c.id = m.channel_id
        ORDER BY p.date DESC
    """,
    "network_view": """
        SELECT handle, title, channel_id, mention_count, discovered_via,
               in_watchlist, first_seen, last_seen
        FROM tg_network_channels
        ORDER BY in_watchlist ASC, mention_count DESC
    """,
    "forwards_view": """
        SELECT c.title AS channel, c.username AS channel_username, p.msg_id, p.date,
               p.fwd_channel_title, p.fwd_channel_username, p.fwd_channel_id,
               p.fwd_msg_id, p.fwd_date, substr(p.text, 1, 200) AS text_preview
        FROM tg_posts p
        LEFT JOIN tg_channels c ON c.id = p.channel_id
        WHERE p.is_forward = 1
        ORDER BY p.date DESC
    """,
    # Joined on imt_id, not nm_id — see the "reviews" query above for why.
    # GROUP BY r.id (not DISTINCT) because unlike the "reviews" query above,
    # this SELECT pulls in i.name/i.category — fields that can differ across
    # colour/size variants sharing one imt_id — so DISTINCT no longer
    # collapses back to one row per review; it was fanning one review out
    # into one CSV row per matching item variant (17 524 rows for 6 380
    # actual flagged reviews, found while fact-checking docs/index.html).
    "field_use_reviews": """
        SELECT r.nm_id, i.name AS item_name, i.category, r.valuation,
               r.field_use_phrase, r.text, r.created_date, r.wb_user_country
        FROM reviews r
        LEFT JOIN items i ON i.imt_id = r.imt_id
        WHERE r.field_use_signal = 1
        GROUP BY r.id
        ORDER BY r.created_date DESC
    """,
    "seller_concentration": """
        SELECT s.supplier_id, s.name, s.full_name, s.inn, s.ogrnip,
               s.entity_type, s.origin, COUNT(i.nm_id) AS n_items,
               GROUP_CONCAT(DISTINCT i.category) AS categories
        FROM sellers s
        JOIN items i ON i.supplier_id = s.supplier_id
        GROUP BY s.supplier_id
        ORDER BY n_items DESC
    """,
    "brand_concentration": """
        SELECT i.brand,
               COUNT(DISTINCT i.supplier_id) AS n_seller_accounts,
               COUNT(*) AS n_items,
               COUNT(DISTINCT CASE WHEN i.military_class='strict_military'
                     THEN i.nm_id END) AS n_strict_military,
               COUNT(DISTINCT CASE WHEN i.military_class='dual_use_demand'
                     THEN i.nm_id END) AS n_dual_use,
               GROUP_CONCAT(DISTINCT s.name) AS sellers,
               GROUP_CONCAT(DISTINCT i.category) AS categories
        FROM items i
        LEFT JOIN sellers s ON s.supplier_id = i.supplier_id
        WHERE i.brand IS NOT NULL AND i.brand != ''
          AND i.military_class IN ('strict_military', 'dual_use_demand')
        GROUP BY i.brand
        ORDER BY n_strict_military DESC, n_seller_accounts DESC, n_items DESC
    """,
    "category_summary": """
        SELECT
            i.category,
            COUNT(DISTINCT i.nm_id) AS n_wb_items,
            (SELECT COUNT(*) FROM ozon_items o WHERE o.category = i.category)
                AS n_ozon_items,
            COUNT(DISTINCT i.supplier_id) AS n_sellers,
            COUNT(DISTINCT CASE WHEN s.origin='cross_border_cn' THEN i.supplier_id END)
                AS n_cn_sellers,
            COALESCE(SUM(latest.feedback_count), 0) AS total_wb_feedback,
            COUNT(DISTINCT rv.id) AS n_reviews,
            COUNT(DISTINCT CASE WHEN rv.field_use_signal=1 THEN rv.id END)
                AS n_confirmed_field_use,
            COUNT(DISTINCT m.id) AS n_tg_mentions,
            COUNT(DISTINCT m.channel_id) AS n_tg_channels
        FROM items i
        LEFT JOIN sellers s ON s.supplier_id = i.supplier_id
        LEFT JOIN (
            SELECT s1.nm_id, s1.feedback_count FROM item_snapshots s1
            WHERE s1.ts = (SELECT MAX(s2.ts) FROM item_snapshots s2
                           WHERE s2.nm_id = s1.nm_id)
        ) latest ON latest.nm_id = i.nm_id
        LEFT JOIN reviews rv ON rv.imt_id = i.imt_id
        LEFT JOIN item_mentions m ON m.nm_id = i.nm_id
        GROUP BY i.category
        ORDER BY n_tg_mentions DESC
    """,
    "ozon_items": "SELECT * FROM ozon_items ORDER BY category, last_seen DESC",
    "military_class_summary": """
        SELECT military_class, military_reason, category, COUNT(*) AS n_items
        FROM items
        WHERE military_class IN ('strict_military', 'dual_use_demand')
        GROUP BY military_class, military_reason, category
        ORDER BY military_class, n_items DESC
    """,
    "strict_military_items": """
        SELECT i.nm_id, i.name, i.category, i.military_reason, s.name AS seller,
               s.entity_type, s.origin,
               'https://www.wildberries.ru/catalog/' || i.nm_id || '/detail.aspx' AS url
        FROM items i
        LEFT JOIN sellers s ON s.supplier_id = i.supplier_id
        WHERE i.military_class = 'strict_military'
        ORDER BY i.category, i.nm_id
    """,
    "delivery_source_summary": """
        SELECT i.delivery_source, i.category, i.military_class,
               COUNT(*) AS n_items,
               COUNT(DISTINCT i.supplier_id) AS n_sellers
        FROM items i
        GROUP BY i.delivery_source, i.category, i.military_class
        ORDER BY i.delivery_source, n_items DESC
    """,
    "wb_policy_violations": """
        SELECT i.nm_id, i.name, i.category, i.military_class,
               i.wb_policy_clause, i.wb_policy_reason,
               s.name AS seller, s.entity_type, s.origin,
               'https://www.wildberries.ru/catalog/' || i.nm_id || '/detail.aspx' AS url
        FROM items i
        LEFT JOIN sellers s ON s.supplier_id = i.supplier_id
        WHERE i.wb_policy_clause IS NOT NULL
        ORDER BY i.wb_policy_clause, i.nm_id
    """,
    "vendor_details": """
        SELECT s.supplier_id, s.name AS seller, s.inn, s.entity_type,
               v.director_name, v.director_role, v.founders, v.ogrn,
               v.founding_date, v.address, v.phones, v.email, v.source_url
        FROM sellers s
        JOIN vendor_details v ON v.inn = s.inn
        ORDER BY s.name
    """,
}

# name -> function(conn) -> list[dict]. Computed in Python (not plain SQL) — mainly
# the pattern-analysis views, which need set/JSON processing SQL can't express
# cleanly. Appended as extra sheets/CSVs alongside QUERIES.
DICT_QUERIES: dict[str, "callable"] = {
    "category_cooccurrence": patterns_mod.category_cooccurrence,
    "temporal_trends": patterns_mod.temporal_trends,
    "recurring_campaigns": patterns_mod.recurring_campaigns,
    "quantities_mentioned": patterns_mod.extract_quantities,
}


def _rows(conn: sqlite3.Connection, sql: str) -> tuple[list[str], list[tuple]]:
    cur = conn.execute(sql)
    headers = [d[0] for d in cur.description]
    return headers, cur.fetchall()


def _dict_rows(conn: sqlite3.Connection, fn) -> tuple[list[str], list[tuple]]:
    records = fn(conn)
    if not records:
        return [], []
    headers = list(records[0].keys())
    rows = [tuple(_dict_cell(r.get(h)) for h in headers) for r in records]
    return headers, rows


def _dict_cell(value: object) -> object:
    # lists (e.g. quantities_mentioned, post_ids) aren't CSV/xlsx-native.
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    return value


def _all_sheets(conn: sqlite3.Connection):
    for name, sql in QUERIES.items():
        yield name, *_rows(conn, sql)
    for name, fn in DICT_QUERIES.items():
        yield name, *_dict_rows(conn, fn)


def export_csv(conn: sqlite3.Connection, out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or config.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, headers, rows in _all_sheets(conn):
        path = out_dir / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            w.writerows(rows)
        written.append(path)
    return written


def export_xlsx(conn: sqlite3.Connection, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or config.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    for name, headers, rows in _all_sheets(conn):
        ws = wb.create_sheet(title=name[:31])
        ws.append(headers)
        for row in rows:
            ws.append([_cell(v) for v in row])
    path = out_dir / "wb_watch.xlsx"
    wb.save(path)
    return path


def _cell(value: object) -> object:
    # openpyxl rejects some types; coerce anything exotic to str.
    if value is None or isinstance(value, (int, float, str)):
        return value
    return str(value)
