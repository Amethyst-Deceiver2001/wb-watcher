"""Discovery sweeps: keyword search, seller catalogs, and already-linked
Telegram mentions.

Newly found nm-ids are tracked immediately (full card+details+reviews+seller) so a
discovery run leaves the DB fully populated, not just with stubs. Items already
tracked are skipped cheaply.

Three independent sweep types:
  - keywords     : WB search API over config/keywords.txt
  - sellers      : full catalog of each vendor seeded in config/seed_sellers.txt.
                   Scoped to seeded vendors only — see discover_by_sellers() for
                   the measured precision that settled that (67% for seeded
                   vendors vs ~7% for every seller holding a tracked item).
  - mentions     : nm_ids already captured in item_mentions (a Telegram post
                   directly linked to this WB card) that were never tracked.
                   No search/pagination involved — a plain DB query plus one
                   card fetch per item — but this was a real, silent gap:
                   tg-scan stores the mention row regardless of whether the
                   item is tracked, and none of the other sweeps ever look at
                   item_mentions, so an explicitly-linked item could sit
                   unresolved indefinitely. Found by inspecting a fresh
                   full-history tg-scan: 48 untracked nm_ids already linked.

A fourth sweep type — one-hop "Смотрите также" (see also) expansion via
in-similar.wildberries.ru (wb/similar.py) — was removed after measurement
showed it ran at roughly half the precision of every other source (50%
categorized / 47% SVO-relevant vs. 88%/81% for keyword/seller discovery).
Tracing why found WB's own recommendation graph chaining a sleeping-bag seed
straight into generic men's shampoo and baby-diaper brands — pure catalog
adjacency in WB's merchandising space, not a military-relevance signal. See
wb/similar.py for the fetch helper, left in place but unused.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from rich.console import Console

from .. import config, db
from ..analysis.military_class import classify_military
from ..wb import search
from . import track_items

console = Console()

# A seller's full catalog or an item's similar-graph neighbors rarely change
# meaningfully day to day — re-walking either within this window just re-spends
# the same network cost for the same (mostly already-tracked) results. Both
# lists only grow as the corpus grows, so without a cooldown every run gets
# more expensive than the last with no ceiling (see discover.py's docstring
# discussion / conversation this was diagnosed in).
_SWEEP_COOLDOWN_DAYS = 7


def _is_stale(swept_at: str | None) -> bool:
    if not swept_at:
        return True
    try:
        ts = datetime.fromisoformat(swept_at)
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts > timedelta(days=_SWEEP_COOLDOWN_DAYS)


def _load_lines(path) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def _load_keywords() -> list[str]:
    return _load_lines(config.KEYWORDS_FILE)


def _load_seed_sellers() -> list[int]:
    out = []
    for line in _load_lines(config.SEED_SELLERS_FILE):
        sid = track_items.parse_seed_seller_line(line)
        if sid:
            out.append(sid)
    return out


def discover_by_keywords(conn: sqlite3.Connection) -> dict[str, int]:
    added = failed = 0
    for query in _load_keywords():
        console.print(f"[cyan]search:[/] {query}")
        for stub in search.keyword_search(query):
            nm_id = stub["nm_id"]
            if db.item_exists(conn, nm_id):
                continue
            result = track_items.track_item(
                conn, nm_id, source="discovery", discovery_query=query
            )
            if result["ok"]:
                added += 1
            else:
                failed += 1
    return {"added": added, "failed": failed}


def discover_by_sellers(conn: sqlite3.Connection) -> dict[str, int]:
    """Sweep the full catalog of every seller seeded in config/seed_sellers.txt.

    Deliberately NOT every seller that has a tracked item. A seller enters the
    DB by selling ONE relevant listing, but their catalog is their whole
    business — usually unrelated consumer goods. Measured page-1 precision
    before enabling this sweep for the first time (it had never run: the
    endpoint was a dead v2 URL until this was fixed):

        hand-seeded vendors (16)                  67% cat / 67% mil
        >=5 tracked items and >=80% military      22% / 27%
        all 3,083 sellers with any tracked item   ~7%
        (keyword discovery, for comparison)       88% / 81%

    Sweeping all 3,083 would have pulled in hundreds of thousands of mostly
    civilian rows against a 12.7k corpus — the same structural error as the
    "смотрите также" graph removed above: expanding from a weak association
    into an unrelated commercial neighbourhood. Widen this only with fresh
    measurement, not by intuition.

    Skips any seller swept within _SWEEP_COOLDOWN_DAYS — without this, the
    seller list (which only grows) gets fully re-paginated every run.

    Per-seller precision is rolled up into `sellers.catalog_hit_rate` on each
    sweep (see db.mark_seller_catalog_swept) rather than assumed static from
    the cohort-level number above — the first real run (2026-08-02, after the
    dead-URL fix) measured 28.6%/19.4% on the two sellers that actually swept,
    well under the 67% cohort baseline that originally justified this scope.
    Revisit the seed list using the per-seller numbers once several sellers
    have real data, rather than re-guessing from the cohort average.
    """
    seller_ids = set(_load_seed_sellers())

    added = failed = skipped = 0
    for sid in sorted(seller_ids):
        if not _is_stale(db.seller_catalog_swept_at(conn, sid)):
            skipped += 1
            continue
        console.print(f"[cyan]seller catalog:[/] {sid}")
        seller_added = seller_military = 0
        for stub in search.seller_catalog(sid):
            nm_id = stub["nm_id"]
            if db.item_exists(conn, nm_id):
                continue
            result = track_items.track_item(
                conn, nm_id, source="discovery", discovery_query=f"seller:{sid}"
            )
            if result["ok"]:
                added += 1
                seller_added += 1
                # classify_military() here (not a query against items.military_class,
                # which stays NULL until the next `wb-watch backfill`) so this sweep's
                # own hit-rate rollup doesn't depend on backfill timing — see
                # db.mark_seller_catalog_swept()'s docstring for why this is tracked.
                mil = classify_military(stub.get("name"), None)
                if mil["military_class"] in ("strict_military", "dual_use_demand"):
                    seller_military += 1
            else:
                failed += 1
        db.mark_seller_catalog_swept(conn, sid, seller_added, seller_military)
    if skipped:
        console.print(
            f"[dim]seller catalog: skipped {skipped} swept within "
            f"{_SWEEP_COOLDOWN_DAYS}d[/]"
        )
    return {"added": added, "failed": failed}


def discover_by_mentions(conn: sqlite3.Connection) -> dict[str, int]:
    """Track any nm_id already captured in item_mentions but never tracked.

    No search/pagination — a plain DB query plus one card fetch per item. This
    is the strongest-confidence source of all (an explicit link in a real
    post) yet none of the other three sweeps ever look at item_mentions.
    """
    nm_ids = [
        r["nm_id"] for r in conn.execute(
            """SELECT DISTINCT m.nm_id FROM item_mentions m
               LEFT JOIN items i ON i.nm_id = m.nm_id
               WHERE m.marketplace='wb' AND m.nm_id IS NOT NULL AND i.nm_id IS NULL"""
        )
    ]
    added = failed = 0
    for nm_id in nm_ids:
        console.print(f"[cyan]mention:[/] {nm_id}")
        result = track_items.track_item(
            conn, nm_id, source="discovery", discovery_query="telegram_mention"
        )
        if result["ok"]:
            added += 1
        else:
            failed += 1
    return {"added": added, "failed": failed}


def run(conn: sqlite3.Connection, sellers: bool = True) -> dict[str, dict[str, int]]:
    by_mentions = discover_by_mentions(conn)
    by_kw = discover_by_keywords(conn)
    by_seller = discover_by_sellers(conn) if sellers else {"added": 0, "failed": 0}
    return {"mentions": by_mentions, "keyword": by_kw, "seller": by_seller}
