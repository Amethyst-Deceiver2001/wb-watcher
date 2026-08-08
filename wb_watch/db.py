"""SQLite store with a snapshot model.

Design intent: WB routinely edits and deletes listings and reviews. To preserve an
evidence trail we UPSERT the current-state tables (items, sellers, tg_channels) and
APPEND to the historical tables (item_snapshots, reviews, tg_posts, item_mentions).
Nothing that was ever observed is overwritten away.

All timestamps are stored as ISO-8601 UTC strings. `now()` is the single source.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from . import config
from .analysis.categorize import categorize_item
from .analysis.military_class import classify_military
from .analysis.wb_policy import classify_wb_policy
from .analysis.sellers import classify_seller
from .analysis.signals import detect_field_use


def _is_locked(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


# WAL mode lets readers proceed freely, but two long-running writers (e.g.
# `discover` and `scan-images` running side by side) can still collide on the
# same brief write transaction — confirmed live: `busy_timeout` alone wasn't
# enough under sustained contention (the other writer never goes idle long
# enough within the wait window). Retrying the write itself, not just
# waiting longer, is what actually survives two continuously-writing
# processes. Safe to retry blindly here since every decorated function is
# either idempotent (INSERT OR REPLACE/IGNORE) or a plain UPDATE.
retry_on_lock = retry(
    retry=retry_if_exception(_is_locked),
    stop=stop_after_attempt(10),
    wait=wait_exponential_jitter(initial=0.5, max=15.0),
    reraise=True,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    nm_id           INTEGER PRIMARY KEY,
    imt_id          INTEGER,
    name            TEXT,
    brand           TEXT,
    brand_id        INTEGER,
    subject_id      INTEGER,
    subj_name       TEXT,
    vendor_code     TEXT,
    supplier_id     INTEGER,
    description     TEXT,
    characteristics TEXT,      -- JSON
    certificate     TEXT,      -- JSON
    wb_create_date  TEXT,
    first_seen      TEXT,
    last_seen       TEXT,
    source          TEXT,      -- seed | discovery | telegram
    discovery_query TEXT,
    flagged_category TEXT
);

CREATE TABLE IF NOT EXISTS item_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nm_id           INTEGER NOT NULL,
    ts              TEXT NOT NULL,
    price_basic     INTEGER,   -- kopecks
    price_product   INTEGER,   -- kopecks (after discount)
    total_qty       INTEGER,
    stocks          TEXT,      -- JSON per-warehouse
    rating          REAL,
    review_rating   REAL,
    feedback_count  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_snapshots_nm ON item_snapshots(nm_id, ts);

CREATE TABLE IF NOT EXISTS sellers (
    supplier_id     INTEGER PRIMARY KEY,
    name            TEXT,
    full_name       TEXT,
    inn             TEXT,
    ogrnip          TEXT,
    kpp             TEXT,
    trademark       TEXT,
    supplier_rating REAL,
    first_seen      TEXT,
    last_seen       TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    id              TEXT PRIMARY KEY,   -- WB feedback id
    nm_id           INTEGER,
    imt_id          INTEGER,
    wb_user_country TEXT,
    text            TEXT,
    pros            TEXT,
    cons            TEXT,
    valuation       INTEGER,
    created_date    TEXT,
    has_photo       INTEGER,
    has_video       INTEGER,
    votes_plus      INTEGER,
    votes_minus     INTEGER,
    seller_answer   TEXT,
    first_seen      TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_nm ON reviews(nm_id);

CREATE TABLE IF NOT EXISTS tg_channels (
    id              INTEGER PRIMARY KEY,   -- telegram channel id
    username        TEXT,
    title           TEXT,
    about           TEXT,
    participants    INTEGER,
    first_scraped   TEXT,
    last_scraped    TEXT,
    last_msg_id     INTEGER
);

CREATE TABLE IF NOT EXISTS tg_posts (
    id              TEXT PRIMARY KEY,   -- "{channel_id}:{msg_id}"
    channel_id      INTEGER,
    msg_id          INTEGER,
    date            TEXT,
    text            TEXT,
    views           INTEGER,
    forwards        INTEGER,
    links           TEXT,      -- JSON list of URLs
    wb_links        TEXT,      -- JSON list of raw wildberries.ru/wb.ru URLs (any path)
    addresses       TEXT,      -- JSON list
    phones          TEXT,      -- JSON list
    payment_details TEXT,      -- JSON list (card #s, SBP, banks)
    is_forward      INTEGER,   -- 1 if this message is itself a forward
    fwd_channel_id  INTEGER,   -- structured forward source (Telethon fwd_from), if resolvable
    fwd_channel_username TEXT,
    fwd_channel_title    TEXT,
    fwd_msg_id      INTEGER,
    fwd_date        TEXT,
    edited_date     TEXT,
    first_seen      TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_channel ON tg_posts(channel_id, msg_id);
-- idx_posts_fwd_channel created in _migrate(), after the fwd_channel_id column
-- migration runs (older DBs won't have that column yet at CREATE TABLE time).

-- Every channel/handle textually referenced (t.me/x, @handle) or structurally
-- forwarded-from across all scraped posts. This is the "extended network" for
-- follow-up: candidates worth adding to config/channels.txt next, not auto-scraped.
CREATE TABLE IF NOT EXISTS tg_network_channels (
    handle          TEXT PRIMARY KEY,  -- lowercased username, no @
    channel_id      INTEGER,           -- filled in if/when resolved via forward metadata
    title           TEXT,
    mention_count   INTEGER DEFAULT 0,
    discovered_via  TEXT,              -- 'text_ref' | 'forward'
    first_seen      TEXT,
    last_seen       TEXT,
    in_watchlist    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS item_mentions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nm_id           INTEGER,           -- may be NULL for ozon-only ids
    marketplace     TEXT,              -- wb | ozon
    external_id     TEXT,              -- raw id as seen (ozon short code or wb nm)
    tg_post_id      TEXT,
    channel_id      INTEGER,
    matched_url     TEXT,
    ts              TEXT,
    UNIQUE(marketplace, external_id, tg_post_id)
);
CREATE INDEX IF NOT EXISTS idx_mentions_nm ON item_mentions(nm_id);

-- Ozon product identity resolved from a short-link redirect (see ozon/resolve.py).
-- Ozon's actual product pages/APIs are bot-walled — this captures only what the
-- redirect hop itself reveals: product id + a descriptive slug, enough to run
-- through the same categorize_item() taxonomy as WB items. No price/seller/reviews.
CREATE TABLE IF NOT EXISTS ozon_items (
    product_id      TEXT PRIMARY KEY,
    short_code      TEXT,
    slug_text       TEXT,
    full_url        TEXT,
    category        TEXT,
    first_seen      TEXT,
    last_seen       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ozon_items_category ON ozon_items(category);

-- Reviews parsed from a user-captured HAR of a product's .../reviews/pdp-part
-- XHR (see ozon/har_parse.py::parse_reviews). Same field_use_signal approach
-- as the WB `reviews` table (analysis/signals.py), kept as a separate table
-- rather than reusing `reviews` since Ozon product_id/review uuid aren't in
-- the same id space as WB's nm_id/feedback id.
CREATE TABLE IF NOT EXISTS ozon_reviews (
    uuid              TEXT PRIMARY KEY,
    product_id        TEXT,
    author_name       TEXT,
    text              TEXT,
    score             INTEGER,
    created_at        TEXT,
    photo_urls        TEXT,
    field_use_signal  INTEGER,
    field_use_phrase  TEXT,
    first_seen        TEXT
);
CREATE INDEX IF NOT EXISTS idx_ozon_reviews_product ON ozon_reviews(product_id);

-- Vendor registry enrichment (director/founders/registration) resolved from a
-- public company registry aggregator. See vendors/lookup.py. Legal entities only
-- (ООО/АО/etc.); sole proprietors' name field already names the individual.
CREATE TABLE IF NOT EXISTS vendor_details (
    inn             TEXT PRIMARY KEY,
    ogrn            TEXT,
    founding_date   TEXT,
    director_name   TEXT,
    director_role   TEXT,
    founders        TEXT,      -- JSON list of {name, inn}
    address         TEXT,
    phones          TEXT,      -- JSON list
    email           TEXT,
    source_url      TEXT,
    first_seen      TEXT,
    last_seen       TEXT
);

-- Gallery-image OCR (see wb/images.py, analysis/ocr.py). Sellers routinely
-- bake promotional text into photos (a soldier captioned "ВЫБОР НАШИХ
-- ГЕРОЕВ") that never appears in the title/description text categorize.py
-- and military_class.py actually see. field_use_signal/phrase mirror
-- reviews' columns of the same name — same detect_field_use() regex, just
-- run over OCR'd image text instead of review text. Also doubles as a
-- curation index for the public site: images with a signal are candidates
-- for illustrating case studies (url is the stable CDN link, no local
-- image storage needed until a specific image is chosen to feature).
CREATE TABLE IF NOT EXISTS item_images (
    nm_id           INTEGER NOT NULL,
    image_index     INTEGER NOT NULL,
    url             TEXT,
    ocr_text        TEXT,
    field_use_signal INTEGER,
    field_use_phrase TEXT,
    first_seen      TEXT,
    PRIMARY KEY (nm_id, image_index)
);
CREATE INDEX IF NOT EXISTS idx_item_images_signal
    ON item_images(field_use_signal);

CREATE TABLE IF NOT EXISTS news_items (
    url             TEXT PRIMARY KEY,
    outlet          TEXT NOT NULL,
    title           TEXT NOT NULL,
    published       TEXT,
    first_seen      TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_items_published
    ON news_items(published);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout=30: sqlite3's default (5s) is too short once two long-running
    # writers (e.g. `discover` and `scan-images`) can run concurrently — a
    # collision should wait out the other's brief write transaction, not
    # raise "database is locked" immediately. WAL mode lets any number of
    # readers proceed regardless; this only affects writer-vs-writer waits.
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# Columns added after the initial release; ALTER TABLE ADD COLUMN for DBs created
# before this change (CREATE TABLE IF NOT EXISTS alone won't add them).
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("tg_posts", "wb_links", "TEXT"),
    ("tg_posts", "is_forward", "INTEGER"),
    ("tg_posts", "fwd_channel_id", "INTEGER"),
    ("tg_posts", "fwd_channel_username", "TEXT"),
    ("tg_posts", "fwd_channel_title", "TEXT"),
    ("tg_posts", "fwd_msg_id", "INTEGER"),
    ("tg_posts", "fwd_date", "TEXT"),
    # Analysis layer: category taxonomy, seller classification, review signals.
    ("items", "category", "TEXT"),
    ("sellers", "entity_type", "TEXT"),
    ("sellers", "origin", "TEXT"),
    ("reviews", "field_use_signal", "INTEGER"),
    ("reviews", "field_use_phrase", "TEXT"),
    ("item_mentions", "ozon_product_id", "TEXT"),
    # Strict-military vs. dual-use-high-demand axis, orthogonal to `category`.
    ("items", "military_class", "TEXT"),
    ("items", "military_reason", "TEXT"),
    # wb_network | seller_warehouse_likely | ambiguous | mixed | unknown — see
    # analysis/delivery.py. Recomputed on backfill as the corpus grows.
    ("items", "delivery_source", "TEXT"),
    # 1 if the last card fetch returned nothing (delisted/removed from WB).
    # Without this, a dead nm-id has no row at all, so item_exists() keeps
    # returning False and every re-mention (e.g. across a tg-scan run) retries
    # the same doomed fetch instead of skipping it.
    ("items", "delisted", "INTEGER"),
    # Discovery cooldown tracking (see pipeline/discover.py): without these,
    # the seller-catalog and similar-items sweeps re-walk their FULL lists
    # every run, and both lists only grow as the corpus grows — each
    # successful discovery run makes the next one more expensive, with no
    # floor. Stamped after a sweep regardless of whether it found anything
    # new, so a recently-checked seller/item is skipped until the cooldown
    # (default 7 days, see discover.py _SWEEP_COOLDOWN_DAYS) elapses.
    ("sellers", "catalog_swept_at", "TEXT"),
    # Per-sweep precision rollup (see pipeline/discover.py discover_by_sellers).
    # The 16-vendor seed scope was justified by a one-time pre-launch precision
    # measurement (67% cat/mil for hand-seeded vendors); these columns let that
    # number be tracked per-seller per-sweep going forward instead of assumed
    # static, since a diversified seller's ratio can drift a lot from the
    # cohort average (measured 2026-08-02: 28.6%/19.4% on the two sellers that
    # actually swept, well under the 67% baseline).
    ("sellers", "catalog_swept_added", "INTEGER"),
    ("sellers", "catalog_swept_military", "INTEGER"),
    ("sellers", "catalog_hit_rate", "REAL"),
    ("items", "similar_swept_at", "TEXT"),
    # Product descriptions were fetched and stored (details.py) but never fed
    # into anything — categorize_item()/classify_military() only ever see
    # name/subj_name. Mirrors reviews.field_use_signal: same detect_field_use()
    # regex, run over description text, kept as its own signal rather than
    # blended into the title-tuned category/military_class taxonomy (long
    # marketing copy is a different false-positive risk profile than titles).
    ("items", "description_signal", "INTEGER"),
    ("items", "description_phrase", "TEXT"),
    # Third axis, orthogonal to category/military_class: does the item fall
    # under a specific numbered clause of Wildberries' own binding seller-
    # contract "List of Prohibited Goods" (see analysis/wb_policy.py)? NULL
    # means no checked clause matched, not "definitely compliant" — only a
    # few clauses relevant to this project are checked.
    ("items", "wb_policy_clause", "TEXT"),
    ("items", "wb_policy_reason", "TEXT"),
    # Richer product data, only obtainable from a user-captured HAR (see
    # ozon/har_parse.py) since Ozon's live product/composer APIs are bot-walled
    # for plain HTTP clients — ozon_items previously stored only what a
    # short-link redirect revealed (product id + slug), no price/seller/specs.
    ("ozon_items", "price_rub", "REAL"),
    ("ozon_items", "price_original_rub", "REAL"),
    ("ozon_items", "rating", "REAL"),
    ("ozon_items", "review_count", "INTEGER"),
    ("ozon_items", "seller_name", "TEXT"),
    ("ozon_items", "seller_reg_number", "TEXT"),
    ("ozon_items", "characteristics", "TEXT"),
    ("ozon_items", "description_text", "TEXT"),
    ("ozon_items", "description_signal", "INTEGER"),
    ("ozon_items", "description_phrase", "TEXT"),
    ("ozon_items", "military_class", "TEXT"),
    ("ozon_items", "military_reason", "TEXT"),
    ("ozon_items", "name", "TEXT"),
    ("ozon_items", "image_urls", "TEXT"),
    ("ozon_items", "har_source", "TEXT"),
    # Stamped when scan_images() fetches an item's card and finds zero
    # gallery photos (pics<=0) — without this, every future scan-images run
    # re-fetches the card for the same ~7,850 zero-photo items from scratch
    # forever, since item_images (the existing skip check) only ever gets a
    # row when there's something to store. See track_items.py::scan_images.
    ("items", "images_checked_at", "TEXT"),
    # 'ru' | 'en' — which page's ticker a headline belongs to (docs/index.html
    # vs docs/en/index.html pull from different, language-scoped outlet
    # lists; see pipeline/news_scan.py's _FEEDS_RU/_FEEDS_EN).
    ("news_items", "lang", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, coltype in _MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_fwd_channel ON tg_posts(fwd_channel_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_items_category ON items(category)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reviews_field_use "
        "ON reviews(field_use_signal)"
    )
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


# --- items ---------------------------------------------------------------

@retry_on_lock
def upsert_item(conn: sqlite3.Connection, item: dict[str, Any]) -> None:
    """Insert a new item or refresh mutable fields; always bumps last_seen.

    `source`/`discovery_query`/`first_seen` are only set on first insert so a
    later re-discovery doesn't overwrite how we originally found it.
    """
    ts = now()
    cols = {
        "imt_id", "name", "brand", "brand_id", "subject_id", "subj_name",
        "vendor_code", "supplier_id", "description", "characteristics",
        "certificate", "wb_create_date", "flagged_category",
    }
    payload = {k: item.get(k) for k in cols}
    payload["characteristics"] = _dumps(item.get("characteristics"))
    payload["certificate"] = _dumps(item.get("certificate"))
    payload["category"] = categorize_item(item.get("name"), item.get("subj_name"))
    mil = classify_military(item.get("name"), item.get("subj_name"))
    payload["military_class"] = mil["military_class"]
    payload["military_reason"] = mil["military_reason"]
    # Third axis, computed here too rather than only in backfill: otherwise
    # newly-discovered items sit with wb_policy_clause NULL until someone
    # remembers to run `wb-watch backfill`, so any count of policy-violating
    # items silently excludes everything found since the last backfill — the
    # other two axes above have always been computed at insert time, and this
    # one lagging behind them was a real, easy-to-miss reporting gap.
    pol = classify_wb_policy(
        item.get("name"), payload["category"], mil["military_class"]
    )
    payload["wb_policy_clause"] = pol["wb_policy_clause"]
    payload["wb_policy_reason"] = pol["wb_policy_reason"]
    desc_phrase = detect_field_use(item.get("description"))
    payload["description_signal"] = 1 if desc_phrase else 0
    payload["description_phrase"] = desc_phrase
    # A successful upsert always comes from a live card fetch — clear any
    # earlier delisted flag in case the listing came back.
    payload["delisted"] = 0

    exists = conn.execute(
        "SELECT 1 FROM items WHERE nm_id=?", (item["nm_id"],)
    ).fetchone()

    if exists:
        set_clause = ", ".join(f"{c}=:{c}" for c in payload)
        conn.execute(
            f"UPDATE items SET {set_clause}, last_seen=:last_seen WHERE nm_id=:nm_id",
            {**payload, "last_seen": ts, "nm_id": item["nm_id"]},
        )
    else:
        conn.execute(
            """INSERT INTO items
               (nm_id, imt_id, name, brand, brand_id, subject_id, subj_name,
                vendor_code, supplier_id, description, characteristics, certificate,
                wb_create_date, category, military_class, military_reason,
                wb_policy_clause, wb_policy_reason,
                description_signal, description_phrase, delisted,
                first_seen, last_seen, source, discovery_query, flagged_category)
               VALUES
               (:nm_id, :imt_id, :name, :brand, :brand_id, :subject_id, :subj_name,
                :vendor_code, :supplier_id, :description, :characteristics, :certificate,
                :wb_create_date, :category, :military_class, :military_reason,
                :wb_policy_clause, :wb_policy_reason,
                :description_signal, :description_phrase, :delisted,
                :first_seen, :last_seen, :source, :discovery_query, :flagged_category)""",
            {
                **payload,
                "nm_id": item["nm_id"],
                "first_seen": ts,
                "last_seen": ts,
                "source": item.get("source", "seed"),
                "discovery_query": item.get("discovery_query"),
            },
        )
    conn.commit()


@retry_on_lock
def mark_delisted(
    conn: sqlite3.Connection, nm_id: int, source: str = "seed",
    discovery_query: str | None = None,
) -> None:
    """Record that a card fetch returned nothing for nm_id — either a bare
    stub (never seen before) or flip the existing row's `delisted` flag,
    without touching any previously-stored name/category/history."""
    ts = now()
    exists = conn.execute("SELECT 1 FROM items WHERE nm_id=?", (nm_id,)).fetchone()
    if exists:
        conn.execute(
            "UPDATE items SET delisted=1, last_seen=? WHERE nm_id=?", (ts, nm_id)
        )
    else:
        conn.execute(
            """INSERT INTO items (nm_id, category, military_class, delisted,
                                   first_seen, last_seen, source, discovery_query)
               VALUES (?, 'other', 'other', 1, ?, ?, ?, ?)""",
            (nm_id, ts, ts, source, discovery_query),
        )
    conn.commit()


def tracked_nm_ids(conn: sqlite3.Connection) -> list[int]:
    return [r["nm_id"] for r in conn.execute("SELECT nm_id FROM items ORDER BY nm_id")]


def item_exists(conn: sqlite3.Connection, nm_id: int) -> bool:
    return conn.execute("SELECT 1 FROM items WHERE nm_id=?", (nm_id,)).fetchone() is not None


# --- snapshots -----------------------------------------------------------

@retry_on_lock
def add_snapshot(conn: sqlite3.Connection, snap: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO item_snapshots
           (nm_id, ts, price_basic, price_product, total_qty, stocks,
            rating, review_rating, feedback_count)
           VALUES (:nm_id, :ts, :price_basic, :price_product, :total_qty, :stocks,
                   :rating, :review_rating, :feedback_count)""",
        {
            "nm_id": snap["nm_id"],
            "ts": now(),
            "price_basic": snap.get("price_basic"),
            "price_product": snap.get("price_product"),
            "total_qty": snap.get("total_qty"),
            "stocks": _dumps(snap.get("stocks")),
            "rating": snap.get("rating"),
            "review_rating": snap.get("review_rating"),
            "feedback_count": snap.get("feedback_count"),
        },
    )
    conn.commit()


# --- sellers -------------------------------------------------------------

@retry_on_lock
def upsert_seller(conn: sqlite3.Connection, seller: dict[str, Any]) -> None:
    ts = now()
    classification = classify_seller(
        seller.get("inn"), seller.get("name"), seller.get("ogrnip")
    )
    seller = {**seller, **classification}
    exists = conn.execute(
        "SELECT 1 FROM sellers WHERE supplier_id=?", (seller["supplier_id"],)
    ).fetchone()
    if exists:
        conn.execute(
            """UPDATE sellers SET name=:name, full_name=:full_name, inn=:inn,
               ogrnip=:ogrnip, kpp=:kpp, trademark=:trademark,
               supplier_rating=:supplier_rating, entity_type=:entity_type,
               origin=:origin, last_seen=:last_seen
               WHERE supplier_id=:supplier_id""",
            {**seller, "last_seen": ts},
        )
    else:
        conn.execute(
            """INSERT INTO sellers
               (supplier_id, name, full_name, inn, ogrnip, kpp, trademark,
                supplier_rating, entity_type, origin, first_seen, last_seen)
               VALUES (:supplier_id, :name, :full_name, :inn, :ogrnip, :kpp,
                       :trademark, :supplier_rating, :entity_type, :origin,
                       :first_seen, :last_seen)""",
            {**seller, "first_seen": ts, "last_seen": ts},
        )
    conn.commit()


def seller_exists(conn: sqlite3.Connection, supplier_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM sellers WHERE supplier_id=?", (supplier_id,)
    ).fetchone() is not None


@retry_on_lock
def mark_seller_catalog_swept(
    conn: sqlite3.Connection,
    supplier_id: int,
    added: int = 0,
    military_hits: int = 0,
) -> None:
    """Stamp catalog_swept_at even for sellers with no `sellers` row yet
    (seed_sellers.txt entries never tracked as an item's seller).

    `added`/`military_hits` roll up this sweep's precision (strict_military +
    dual_use_demand out of newly-tracked items) so a seller's catalog can be
    judged on measured evidence rather than the one-time cohort-level
    precision check that originally justified the seed scope. `catalog_hit_rate`
    is left NULL (not 0) when `added` is 0 — nothing was measured, which is a
    different fact than "measured and found zero signal".
    """
    ts = now()
    hit_rate = (military_hits / added) if added else None
    conn.execute(
        """INSERT INTO sellers
           (supplier_id, first_seen, last_seen, catalog_swept_at,
            catalog_swept_added, catalog_swept_military, catalog_hit_rate)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(supplier_id) DO UPDATE SET
               catalog_swept_at=excluded.catalog_swept_at,
               catalog_swept_added=excluded.catalog_swept_added,
               catalog_swept_military=excluded.catalog_swept_military,
               catalog_hit_rate=excluded.catalog_hit_rate""",
        (supplier_id, ts, ts, ts, added, military_hits, hit_rate),
    )
    conn.commit()


def seller_catalog_swept_at(conn: sqlite3.Connection, supplier_id: int) -> str | None:
    row = conn.execute(
        "SELECT catalog_swept_at FROM sellers WHERE supplier_id=?", (supplier_id,)
    ).fetchone()
    return row["catalog_swept_at"] if row else None


@retry_on_lock
def mark_item_similar_swept(conn: sqlite3.Connection, nm_id: int) -> None:
    conn.execute(
        "UPDATE items SET similar_swept_at=? WHERE nm_id=?", (now(), nm_id)
    )
    conn.commit()


# --- reviews -------------------------------------------------------------

@retry_on_lock
def add_reviews(conn: sqlite3.Connection, reviews: Iterable[dict[str, Any]]) -> int:
    """Insert reviews we haven't seen. Returns count of newly stored rows.

    Every review is scanned for an explicit front-line/combat-use confirmation
    (see analysis/signals.py) at ingestion time — this is the strongest evidence
    class in the dataset, so it's captured as soon as the review is first seen.
    """
    ts = now()
    added = 0
    for r in reviews:
        # WB splits a review into "Достоинства"/"Недостатки"/comment
        # (pros/cons/text) — many buyers put the whole confirmation in pros
        # alone (e.g. pros="Заказ на СВО", text="") and leave text empty, so
        # scanning text only silently missed these. Found via a body-bag
        # listing whose only two textual reviews were "Заказ на СВО"/"Брали
        # на СВО" in pros, both stored with field_use_signal=0.
        combined = " ".join(filter(None, [r.get("text"), r.get("pros"), r.get("cons")]))
        phrase = detect_field_use(combined)
        cur = conn.execute(
            """INSERT OR IGNORE INTO reviews
               (id, nm_id, imt_id, wb_user_country, text, pros, cons, valuation,
                created_date, has_photo, has_video, votes_plus, votes_minus,
                seller_answer, field_use_signal, field_use_phrase, first_seen)
               VALUES (:id, :nm_id, :imt_id, :wb_user_country, :text, :pros, :cons,
                       :valuation, :created_date, :has_photo, :has_video,
                       :votes_plus, :votes_minus, :seller_answer, :field_use_signal,
                       :field_use_phrase, :first_seen)""",
            {
                **r,
                "field_use_signal": 1 if phrase else 0,
                "field_use_phrase": phrase,
                "first_seen": ts,
            },
        )
        added += cur.rowcount
    conn.commit()
    return added


# --- images ---------------------------------------------------------------

@retry_on_lock
def add_image_analysis(
    conn: sqlite3.Connection, nm_id: int, results: list[dict[str, Any]]
) -> int:
    """Store OCR results for one item's gallery. `results` items carry
    image_index, url, ocr_text, field_use_phrase (see analysis/ocr.py).
    Idempotent per (nm_id, image_index) — re-running overwrites with the
    latest OCR pass rather than accumulating duplicates, since a gallery
    image at a given index is replaceable content, not an append-only log
    like a review."""
    ts = now()
    added = 0
    for r in results:
        cur = conn.execute(
            """INSERT OR REPLACE INTO item_images
               (nm_id, image_index, url, ocr_text, field_use_signal,
                field_use_phrase, first_seen)
               VALUES (:nm_id, :image_index, :url, :ocr_text,
                       :field_use_signal, :field_use_phrase, :first_seen)""",
            {
                **r,
                "nm_id": nm_id,
                "field_use_signal": 1 if r.get("field_use_phrase") else 0,
                "first_seen": ts,
            },
        )
        added += cur.rowcount
    conn.commit()
    return added


def image_analysis_exists(conn: sqlite3.Connection, nm_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM item_images WHERE nm_id=?", (nm_id,)
    ).fetchone() is not None


def image_analysis_count(conn: sqlite3.Connection, nm_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM item_images WHERE nm_id=?", (nm_id,)
    ).fetchone()[0]


def images_checked_at(conn: sqlite3.Connection, nm_id: int) -> str | None:
    row = conn.execute(
        "SELECT images_checked_at FROM items WHERE nm_id=?", (nm_id,)
    ).fetchone()
    return row["images_checked_at"] if row else None


@retry_on_lock
def mark_images_checked(conn: sqlite3.Connection, nm_id: int) -> None:
    """Stamp an item as checked-and-confirmed-zero-photos (see scan_images).
    Separate from mark_delisted — a live item can legitimately have pics=0
    on a given size/color variant without being delisted."""
    conn.execute(
        "UPDATE items SET images_checked_at=? WHERE nm_id=?", (now(), nm_id)
    )
    conn.commit()


# --- telegram ------------------------------------------------------------

@retry_on_lock
def upsert_channel(conn: sqlite3.Connection, ch: dict[str, Any]) -> None:
    ts = now()
    row = conn.execute(
        "SELECT last_msg_id FROM tg_channels WHERE id=?", (ch["id"],)
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE tg_channels SET username=:username, title=:title, about=:about,
               participants=:participants, last_scraped=:last_scraped,
               last_msg_id=:last_msg_id WHERE id=:id""",
            {**ch, "last_scraped": ts},
        )
    else:
        conn.execute(
            """INSERT INTO tg_channels
               (id, username, title, about, participants, first_scraped,
                last_scraped, last_msg_id)
               VALUES (:id, :username, :title, :about, :participants,
                       :first_scraped, :last_scraped, :last_msg_id)""",
            {**ch, "first_scraped": ts, "last_scraped": ts},
        )
    conn.commit()


def channel_last_msg_id(conn: sqlite3.Connection, channel_id: int) -> int:
    row = conn.execute(
        "SELECT last_msg_id FROM tg_channels WHERE id=?", (channel_id,)
    ).fetchone()
    return (row["last_msg_id"] if row and row["last_msg_id"] else 0)


@retry_on_lock
def add_post(conn: sqlite3.Connection, post: dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO tg_posts
           (id, channel_id, msg_id, date, text, views, forwards, links, wb_links,
            addresses, phones, payment_details, is_forward, fwd_channel_id,
            fwd_channel_username, fwd_channel_title, fwd_msg_id, fwd_date,
            edited_date, first_seen)
           VALUES (:id, :channel_id, :msg_id, :date, :text, :views, :forwards,
                   :links, :wb_links, :addresses, :phones, :payment_details,
                   :is_forward, :fwd_channel_id, :fwd_channel_username,
                   :fwd_channel_title, :fwd_msg_id, :fwd_date, :edited_date,
                   :first_seen)""",
        {
            "id": f"{post['channel_id']}:{post['msg_id']}",
            "channel_id": post["channel_id"],
            "msg_id": post["msg_id"],
            "date": post.get("date"),
            "text": post.get("text"),
            "views": post.get("views"),
            "forwards": post.get("forwards"),
            "links": _dumps(post.get("links")),
            "wb_links": _dumps(post.get("wb_links")),
            "addresses": _dumps(post.get("addresses")),
            "phones": _dumps(post.get("phones")),
            "payment_details": _dumps(post.get("payment_details")),
            "is_forward": 1 if post.get("is_forward") else 0,
            "fwd_channel_id": post.get("fwd_channel_id"),
            "fwd_channel_username": post.get("fwd_channel_username"),
            "fwd_channel_title": post.get("fwd_channel_title"),
            "fwd_msg_id": post.get("fwd_msg_id"),
            "fwd_date": post.get("fwd_date"),
            "edited_date": post.get("edited_date"),
            "first_seen": now(),
        },
    )
    conn.commit()


@retry_on_lock
def upsert_network_channel(conn: sqlite3.Connection, handle: str,
                            discovered_via: str, title: str | None = None,
                            channel_id: int | None = None) -> None:
    """Record/bump a channel referenced (textually or via forward) by scraped posts.

    This is the "extended network" — candidates for follow-up, not auto-added to
    the watchlist. `in_watchlist` is refreshed from config each scan so exports show
    which candidates are already covered.
    """
    handle = handle.lower().lstrip("@")
    ts = now()
    row = conn.execute(
        "SELECT mention_count FROM tg_network_channels WHERE handle=?", (handle,)
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE tg_network_channels
               SET mention_count = mention_count + 1, last_seen = :ts,
                   title = COALESCE(:title, title),
                   channel_id = COALESCE(:channel_id, channel_id)
               WHERE handle = :handle""",
            {"ts": ts, "title": title, "channel_id": channel_id, "handle": handle},
        )
    else:
        conn.execute(
            """INSERT INTO tg_network_channels
               (handle, channel_id, title, mention_count, discovered_via,
                first_seen, last_seen, in_watchlist)
               VALUES (:handle, :channel_id, :title, 1, :discovered_via,
                       :ts, :ts, 0)""",
            {"handle": handle, "channel_id": channel_id, "title": title,
             "discovered_via": discovered_via, "ts": ts},
        )
    conn.commit()


@retry_on_lock
def mark_watchlist_channels(conn: sqlite3.Connection, handles: list[str]) -> None:
    normalized = {h.lower().lstrip("@") for h in handles}
    conn.execute("UPDATE tg_network_channels SET in_watchlist = 0")
    for h in normalized:
        conn.execute(
            "UPDATE tg_network_channels SET in_watchlist = 1 WHERE handle = ?", (h,)
        )
    conn.commit()


@retry_on_lock
def add_mention(conn: sqlite3.Connection, m: dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO item_mentions
           (nm_id, marketplace, external_id, tg_post_id, channel_id, matched_url, ts)
           VALUES (:nm_id, :marketplace, :external_id, :tg_post_id, :channel_id,
                   :matched_url, :ts)""",
        {**m, "ts": now()},
    )
    conn.commit()


# --- ozon (short-link resolution only; see ozon/resolve.py) --------------

def unresolved_ozon_codes(conn: sqlite3.Connection) -> list[str]:
    """Distinct Ozon short codes cited in item_mentions with no ozon_items row yet."""
    rows = conn.execute(
        """SELECT DISTINCT m.external_id FROM item_mentions m
           WHERE m.marketplace = 'ozon'
             AND m.external_id NOT IN (SELECT short_code FROM ozon_items)"""
    ).fetchall()
    return [r["external_id"] for r in rows]


@retry_on_lock
def upsert_ozon_item(conn: sqlite3.Connection, item: dict[str, Any]) -> None:
    ts = now()
    category = categorize_item(item.get("slug_text"))
    payload = {**item, "category": category}
    exists = conn.execute(
        "SELECT 1 FROM ozon_items WHERE product_id=?", (item["product_id"],)
    ).fetchone()
    if exists:
        conn.execute(
            """UPDATE ozon_items SET short_code=:short_code, slug_text=:slug_text,
               full_url=:full_url, category=:category, last_seen=:last_seen
               WHERE product_id=:product_id""",
            {**payload, "last_seen": ts},
        )
    else:
        conn.execute(
            """INSERT INTO ozon_items
               (product_id, short_code, slug_text, full_url, category,
                first_seen, last_seen)
               VALUES (:product_id, :short_code, :slug_text, :full_url, :category,
                       :first_seen, :last_seen)""",
            {**payload, "first_seen": ts, "last_seen": ts},
        )
    conn.execute(
        "UPDATE item_mentions SET ozon_product_id=? WHERE marketplace='ozon' AND external_id=?",
        (item["product_id"], item["short_code"]),
    )
    conn.commit()


# --- ozon (HAR-derived: see ozon/har_parse.py, pipeline/import_ozon_har.py) ---
# All of the below is offline parsing of a user-captured HAR — no network
# calls happen here, so unlike resolve_ozon.py this is not a "crawling job"
# under CLAUDE.md and callers may run it directly.

@retry_on_lock
def upsert_ozon_listing_item(
    conn: sqlite3.Connection, item: dict[str, Any], har_source: str
) -> None:
    """From a category/search/brand/seller listing tile (har_parse.parse_listing_grid).
    Cheaper than a full product-page parse, so it's fine to call for every tile
    seen even though most fields may already exist from a richer detail parse —
    COALESCE keeps whichever detail fields are already populated."""
    ts = now()
    category = categorize_item(item.get("name"))
    mil = classify_military(item.get("name"), None)
    exists = conn.execute(
        "SELECT 1 FROM ozon_items WHERE product_id=?", (item["product_id"],)
    ).fetchone()
    payload = {
        "product_id": item["product_id"],
        "name": item.get("name"),
        "full_url": item.get("link"),
        "price_rub": item.get("price_rub"),
        "price_original_rub": item.get("price_original_rub"),
        "rating": item.get("rating"),
        "review_count": item.get("review_count"),
        "image_urls": ", ".join(item.get("image_urls") or []) or None,
        "category": category,
        "military_class": mil.get("military_class"),
        "military_reason": mil.get("military_reason"),
        "har_source": har_source,
        "last_seen": ts,
        "first_seen": ts,
    }
    if exists:
        conn.execute(
            """UPDATE ozon_items SET
                 name=COALESCE(:name, name),
                 full_url=COALESCE(:full_url, full_url),
                 price_rub=COALESCE(:price_rub, price_rub),
                 price_original_rub=COALESCE(:price_original_rub, price_original_rub),
                 rating=COALESCE(:rating, rating),
                 review_count=COALESCE(:review_count, review_count),
                 image_urls=COALESCE(:image_urls, image_urls),
                 category=COALESCE(:category, category),
                 military_class=COALESCE(:military_class, military_class),
                 military_reason=COALESCE(:military_reason, military_reason),
                 har_source=:har_source, last_seen=:last_seen
               WHERE product_id=:product_id""",
            payload,
        )
    else:
        conn.execute(
            """INSERT INTO ozon_items
               (product_id, name, full_url, price_rub, price_original_rub,
                rating, review_count, image_urls, category, military_class,
                military_reason, har_source, first_seen, last_seen)
               VALUES (:product_id, :name, :full_url, :price_rub, :price_original_rub,
                       :rating, :review_count, :image_urls, :category, :military_class,
                       :military_reason, :har_source, :first_seen, :last_seen)""",
            payload,
        )
    conn.commit()


@retry_on_lock
def upsert_ozon_item_detail(
    conn: sqlite3.Connection, product_id: str, detail: dict[str, Any], har_source: str
) -> None:
    """From a full product-page parse (har_parse.parse_product_page +
    parse_characteristics_and_description) — the richer fields a listing tile
    doesn't carry: seller identity, full characteristics, description text."""
    ts = now()
    name = detail.get("title")
    description = detail.get("description_text")
    desc_signal_phrase = detect_field_use(description) if description else None
    # Skip when name is absent (e.g. a characteristics/description-only call
    # from the entrypoint-api JSON pass) — classify_military(None, None)
    # would return a real "other"/"uncertain" value from empty text, which
    # COALESCE below would then wrongly clobber an existing title-based
    # classification with.
    mil = classify_military(name, None) if name else {}
    exists = conn.execute(
        "SELECT 1 FROM ozon_items WHERE product_id=?", (product_id,)
    ).fetchone()
    payload = {
        "product_id": product_id,
        "name": name,
        "full_url": detail.get("og_url"),
        "price_rub": detail.get("price_rub"),
        "price_original_rub": detail.get("price_original_rub"),
        "seller_name": detail.get("seller_name"),
        "seller_reg_number": detail.get("seller_reg_number"),
        "characteristics": json.dumps(detail.get("characteristics"), ensure_ascii=False)
        if detail.get("characteristics")
        else None,
        "description_text": description,
        "description_signal": 1 if desc_signal_phrase else 0,
        "description_phrase": desc_signal_phrase,
        "category": categorize_item(name) if name else None,
        "military_class": mil.get("military_class"),
        "military_reason": mil.get("military_reason"),
        "har_source": har_source,
        "last_seen": ts,
        "first_seen": ts,
    }
    if exists:
        conn.execute(
            """UPDATE ozon_items SET
                 name=COALESCE(:name, name),
                 full_url=COALESCE(:full_url, full_url),
                 price_rub=COALESCE(:price_rub, price_rub),
                 price_original_rub=COALESCE(:price_original_rub, price_original_rub),
                 seller_name=COALESCE(:seller_name, seller_name),
                 seller_reg_number=COALESCE(:seller_reg_number, seller_reg_number),
                 characteristics=COALESCE(:characteristics, characteristics),
                 description_text=COALESCE(:description_text, description_text),
                 description_signal=COALESCE(:description_signal, description_signal),
                 description_phrase=COALESCE(:description_phrase, description_phrase),
                 category=COALESCE(:category, category),
                 military_class=COALESCE(:military_class, military_class),
                 military_reason=COALESCE(:military_reason, military_reason),
                 har_source=:har_source, last_seen=:last_seen
               WHERE product_id=:product_id""",
            payload,
        )
    else:
        conn.execute(
            """INSERT INTO ozon_items
               (product_id, name, full_url, price_rub, price_original_rub,
                seller_name, seller_reg_number, characteristics, description_text,
                description_signal, description_phrase, category, military_class,
                military_reason, har_source, first_seen, last_seen)
               VALUES (:product_id, :name, :full_url, :price_rub, :price_original_rub,
                       :seller_name, :seller_reg_number, :characteristics, :description_text,
                       :description_signal, :description_phrase, :category, :military_class,
                       :military_reason, :har_source, :first_seen, :last_seen)""",
            payload,
        )
    conn.commit()


@retry_on_lock
def add_ozon_review(conn: sqlite3.Connection, review: dict[str, Any]) -> None:
    """INSERT OR REPLACE by review uuid — idempotent, safe to re-import the
    same HAR (e.g. after capturing more pages of the same product's reviews)."""
    if not review.get("uuid"):
        return
    text = review.get("text")
    phrase = detect_field_use(text) if text else None
    created_at = None
    if review.get("created_at_unix"):
        created_at = datetime.fromtimestamp(
            review["created_at_unix"], tz=timezone.utc
        ).isoformat()
    conn.execute(
        """INSERT INTO ozon_reviews
           (uuid, product_id, author_name, text, score, created_at, photo_urls,
            field_use_signal, field_use_phrase, first_seen)
           VALUES (:uuid, :product_id, :author_name, :text, :score, :created_at,
                   :photo_urls, :field_use_signal, :field_use_phrase, :first_seen)
           ON CONFLICT(uuid) DO UPDATE SET
               text=excluded.text, score=excluded.score,
               photo_urls=excluded.photo_urls,
               field_use_signal=excluded.field_use_signal,
               field_use_phrase=excluded.field_use_phrase""",
        {
            "uuid": review["uuid"],
            "product_id": review.get("product_id"),
            "author_name": review.get("author_name"),
            "text": text,
            "score": review.get("score"),
            "created_at": created_at,
            "photo_urls": ", ".join(review.get("photo_urls") or []) or None,
            "field_use_signal": 1 if phrase else 0,
            "field_use_phrase": phrase,
            "first_seen": now(),
        },
    )
    conn.commit()


# --- vendor registry enrichment --------------------------------------------

def unresolved_vendor_inns(conn: sqlite3.Connection) -> list[str]:
    """Legal-entity sellers with an INN but no vendor_details row yet."""
    rows = conn.execute(
        """SELECT DISTINCT inn FROM sellers
           WHERE entity_type = 'legal_entity_ru' AND inn IS NOT NULL
             AND inn NOT IN (SELECT inn FROM vendor_details)"""
    ).fetchall()
    return [r["inn"] for r in rows]


@retry_on_lock
def upsert_vendor_details(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    ts = now()
    payload = {
        "inn": data["inn"],
        "ogrn": data.get("ogrn"),
        "founding_date": data.get("founding_date"),
        "director_name": data.get("director_name"),
        "director_role": data.get("director_role"),
        "founders": _dumps(data.get("founders")),
        "address": data.get("address"),
        "phones": _dumps(data.get("phones")),
        "email": data.get("email"),
        "source_url": data.get("source_url"),
    }
    exists = conn.execute(
        "SELECT 1 FROM vendor_details WHERE inn=?", (payload["inn"],)
    ).fetchone()
    if exists:
        conn.execute(
            """UPDATE vendor_details SET ogrn=:ogrn, founding_date=:founding_date,
               director_name=:director_name, director_role=:director_role,
               founders=:founders, address=:address, phones=:phones, email=:email,
               source_url=:source_url, last_seen=:last_seen
               WHERE inn=:inn""",
            {**payload, "last_seen": ts},
        )
    else:
        conn.execute(
            """INSERT INTO vendor_details
               (inn, ogrn, founding_date, director_name, director_role, founders,
                address, phones, email, source_url, first_seen, last_seen)
               VALUES (:inn, :ogrn, :founding_date, :director_name, :director_role,
                       :founders, :address, :phones, :email, :source_url,
                       :first_seen, :last_seen)""",
            {**payload, "first_seen": ts, "last_seen": ts},
        )
    conn.commit()


# --- news ticker ------------------------------------------------------------

def add_news_items(conn: sqlite3.Connection, items: Iterable[dict[str, Any]]) -> int:
    """Insert news headlines we haven't seen (by URL). Returns count added.
    Each item must include 'lang' ('ru' or 'en') — see news_scan.py."""
    ts = now()
    added = 0
    for it in items:
        cur = conn.execute(
            """INSERT OR IGNORE INTO news_items
               (url, outlet, title, published, lang, first_seen)
               VALUES (:url, :outlet, :title, :published, :lang, :first_seen)""",
            {**it, "first_seen": ts},
        )
        added += cur.rowcount
    conn.commit()
    return added


def recent_news_items(
    conn: sqlite3.Connection, limit: int = 8, lang: str = "ru"
) -> list[sqlite3.Row]:
    # lang IS NULL treated as 'ru': every row stored before the lang column
    # existed came from the (at the time, only) Russian-outlet scan.
    return conn.execute(
        "SELECT outlet, title, url, published FROM news_items "
        "WHERE COALESCE(lang, 'ru') = ? "
        "ORDER BY COALESCE(published, first_seen) DESC LIMIT ?",
        (lang, limit),
    ).fetchall()
