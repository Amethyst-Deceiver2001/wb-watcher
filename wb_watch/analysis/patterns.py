"""Pattern analysis over the Telegram crowdfunding corpus already collected.

Pure local recomputation over data already in the DB — no network calls, safe to run
directly. Four angles requested: category co-occurrence within a post, temporal
trends in post/category volume, recurring-campaign detection (payment details reused
across channels/dates — the same fundraiser posting to multiple audiences), and
quantity extraction from post text near an item mention.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any

_QTY_RE = re.compile(
    r"(\d+)\s*(?:шт\.?|штук[аи]?|компл(?:ект(?:ов|а)?)?\.?|пар[ы]?|уп\.?|упаковк[аи])",
    re.I,
)

# Card numbers are collected for pattern-matching (same card reused across
# posts/channels), never for use — publishing them in full would let anyone
# make a payment against a real donation card. Masked to first6+••••+last4
# (enough digits to remain a stable join key across occurrences of the same
# card, not enough to be usable) everywhere this leaves the DB, per the
# masking commitment in RESEARCH_BRIEF.md §7.
_CARD_DIGITS_RE = re.compile(r"\d{12,19}")


def mask_card_number(digits: str) -> str:
    if len(digits) <= 10:
        return "•" * len(digits)
    return f"{digits[:6]}••••{digits[-4:]}"


def mask_payment_detail(detail: str) -> str:
    """Mask any card-length digit run inside a `card:...`/free-text payment
    detail string. Non-card details (bank names, SBP phone numbers) pass
    through unchanged — only actual card-number-length digit runs are risky."""
    return _CARD_DIGITS_RE.sub(lambda m: mask_card_number(m.group(0)), detail)


def category_cooccurrence(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Category pairs mentioned in the same Telegram post (WB + Ozon combined).

    A post that links both a tourniquet and an FPV frame is evidence the same
    fundraiser/unit is sourcing across categories in one drive — useful for finding
    bundled procurement rather than treating each category as an isolated market.
    """
    rows = conn.execute(
        """SELECT m.tg_post_id AS post_id,
                  COALESCE(i.category, o.category) AS category
           FROM item_mentions m
           LEFT JOIN items i ON m.marketplace = 'wb' AND i.nm_id = m.nm_id
           LEFT JOIN ozon_items o ON m.marketplace = 'ozon' AND o.short_code = m.external_id
           WHERE COALESCE(i.category, o.category) IS NOT NULL"""
    ).fetchall()

    by_post: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        by_post[r["post_id"]].add(r["category"])

    pair_counts: Counter[tuple[str, str]] = Counter()
    for cats in by_post.values():
        for a, b in combinations(sorted(cats), 2):
            pair_counts[(a, b)] += 1

    return [
        {"category_a": a, "category_b": b, "co_occurring_posts": n}
        for (a, b), n in pair_counts.most_common()
    ]


def temporal_trends(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Monthly post volume and category mix — is demand for a category rising,
    falling, or flat over the corpus window?"""
    rows = conn.execute(
        """SELECT substr(p.date, 1, 7) AS month,
                  COALESCE(i.category, o.category) AS category,
                  COUNT(DISTINCT m.tg_post_id) AS mentions
           FROM item_mentions m
           JOIN tg_posts p ON p.id = m.tg_post_id
           LEFT JOIN items i ON m.marketplace = 'wb' AND i.nm_id = m.nm_id
           LEFT JOIN ozon_items o ON m.marketplace = 'ozon' AND o.short_code = m.external_id
           WHERE p.date IS NOT NULL AND COALESCE(i.category, o.category) IS NOT NULL
           GROUP BY 1, 2
           ORDER BY 1"""
    ).fetchall()
    return [dict(r) for r in rows]


def recurring_campaigns(conn: sqlite3.Connection, min_occurrences: int = 2) -> list[dict[str, Any]]:
    """Card/SBP numbers reused across multiple posts.

    Same card number appearing under different channels/dates is a fundraiser
    running the same campaign across audiences (or the same unit's standing
    collection point) rather than a one-off ask — useful for tracing a single
    procurement effort's real reach independent of channel-follower counts.
    Deliberately keys only on `card:` entries — `bank:` entries just name a bank
    (e.g. "Сбер", "СБП") with no per-fundraiser specificity and would make every
    campaign look "reused" by coincidence.
    """
    rows = conn.execute(
        """SELECT id, channel_id, date, payment_details FROM tg_posts
           WHERE payment_details LIKE '%card:%'"""
    ).fetchall()

    by_detail: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        try:
            details = json.loads(r["payment_details"]) or []
        except (TypeError, ValueError):
            continue
        for d in details:
            d = d.strip() if isinstance(d, str) else d
            if not d or not d.startswith("card:"):
                continue
            by_detail[d].append(
                {"post_id": r["id"], "channel_id": r["channel_id"], "date": r["date"]}
            )

    campaigns = []
    for detail, sightings in by_detail.items():
        channels = {s["channel_id"] for s in sightings}
        if len(sightings) >= min_occurrences and len(channels) >= 2:
            dates = sorted(s["date"] for s in sightings if s["date"])
            campaigns.append({
                # Masked here, not at the dict-key/dedup stage above — dedup
                # must operate on the real card digits so two masked-alike
                # numbers never collapse into one row.
                "payment_detail": mask_payment_detail(detail),
                "occurrences": len(sightings),
                "distinct_channels": len(channels),
                "first_seen": dates[0] if dates else None,
                "last_seen": dates[-1] if dates else None,
                "post_ids": [s["post_id"] for s in sightings],
            })
    campaigns.sort(key=lambda c: c["distinct_channels"], reverse=True)
    return campaigns


def extract_quantities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Quantity mentions ("X шт"/"X компл") in posts that also link a tracked item —
    a rough demand-volume signal per mention, not just a yes/no count."""
    rows = conn.execute(
        """SELECT m.tg_post_id AS post_id, m.marketplace, m.external_id, m.nm_id,
                  p.text, COALESCE(i.category, o.category) AS category
           FROM item_mentions m
           JOIN tg_posts p ON p.id = m.tg_post_id
           LEFT JOIN items i ON m.marketplace = 'wb' AND i.nm_id = m.nm_id
           LEFT JOIN ozon_items o ON m.marketplace = 'ozon' AND o.short_code = m.external_id
           WHERE p.text IS NOT NULL"""
    ).fetchall()

    results = []
    for r in rows:
        matches = _QTY_RE.findall(r["text"] or "")
        if not matches:
            continue
        results.append({
            "post_id": r["post_id"],
            "marketplace": r["marketplace"],
            "external_id": r["external_id"],
            "category": r["category"],
            "quantities_mentioned": [int(m) for m in matches],
            "max_quantity": max(int(m) for m in matches),
        })
    results.sort(key=lambda x: x["max_quantity"], reverse=True)
    return results


def run_all(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "category_cooccurrence": category_cooccurrence(conn),
        "temporal_trends": temporal_trends(conn),
        "recurring_campaigns": recurring_campaigns(conn),
        "quantities": extract_quantities(conn),
    }
