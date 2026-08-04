"""Central configuration: env loading, paths, and Wildberries endpoint constants.

Endpoint versions drift over time (WB rotates card/search API versions and 404s the
old ones). They live here as constants so a single edit re-points the whole tool.
Verified live at build time: card v4, search v5, feedbacks v2.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = two levels up from this file (wb_watch/config.py -> repo root).
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str) -> str:
    val = os.environ.get(name, "")
    return val if val.strip() else default


# --- Paths ---
DB_PATH = ROOT / _env("WB_DB_PATH", "data/wb_watch.db")
EXPORT_DIR = ROOT / _env("WB_EXPORT_DIR", "exports")
MEDIA_DIR = ROOT / "data" / "media"
CONFIG_DIR = ROOT / "config"
SEED_ITEMS_FILE = CONFIG_DIR / "seed_items.txt"
SEED_SELLERS_FILE = CONFIG_DIR / "seed_sellers.txt"
KEYWORDS_FILE = CONFIG_DIR / "keywords.txt"
CHANNELS_FILE = CONFIG_DIR / "channels.txt"

# --- Telegram ---
TG_API_ID = os.environ.get("TG_API_ID", "").strip()
TG_API_HASH = os.environ.get("TG_API_HASH", "").strip()
TG_SESSION = os.environ.get("TG_SESSION", "").strip()

# --- Wildberries request tunables ---
WB_DEST = _env("WB_DEST", "-1257786")
WB_MIN_INTERVAL = float(_env("WB_MIN_INTERVAL", "0.6"))
# Bumped from 6: a large keyword sweep (thousands of sequential new nm-ids)
# can hit sustained rate-limiting where 6 attempts at up to 30s backoff isn't
# enough cushion — items that exhaust retries are silently dropped (no row,
# not even a delisted stub), so under-retrying here directly costs coverage.
WB_MAX_RETRIES = int(_env("WB_MAX_RETRIES", "10"))

# --- Ozon request tunables ---
# Confirmed live: Ozon's short-link redirect endpoint applies a much tighter
# burst-rate limit than WB — a handful of requests within a few seconds triggers a
# temporary 403 that a single request after a short cooldown sails past. Needs its
# own, much slower throttle; do not share WB's.
OZON_MIN_INTERVAL = float(_env("OZON_MIN_INTERVAL", "8.0"))

# --- list-org.com (vendor registry) request tunables ---
# Confirmed live: after a handful of requests in quick succession, list-org starts
# 307-redirecting to a /bot challenge page — the same IP-based burst-rate defense
# pattern as Ozon. Own slow throttle, not shared with WB or Ozon.
VENDOR_LOOKUP_MIN_INTERVAL = float(_env("VENDOR_LOOKUP_MIN_INTERVAL", "10.0"))

# --- Endpoint templates (versions are the parts that drift) ---
CARD_URL = "https://card.wb.ru/cards/v4/detail"
SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v5/search"
SELLER_CATALOG_URL = "https://catalog.wb.ru/sellers/v4/catalog"
SELLER_INFO_URL = "https://static-basket-01.wbbasket.ru/vol0/data/supplier-by-id/{sid}.json"
# Feedbacks: two mirror hosts; {n} in (1, 2). Keyed on imt_id (product `root`).
FEEDBACKS_URL = "https://feedbacks{n}.wb.ru/feedbacks/v2/{imt_id}"
# Full card json (description + characteristics); host resolved in wb/basket.py.
BASKET_CARD_URL = "https://basket-{host:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/card.json"
# "Смотрите также" / similar-products: returns a flat JSON array of related nm-ids.
# No query params needed beyond nm; verified live, no auth/anti-bot gate (unlike the
# main wildberries.ru site, which 498s plain HTTP clients).
SIMILAR_URL = "https://in-similar.wildberries.ru/"

# Common query params shared by card/search/catalog endpoints.
COMMON_PARAMS = {
    "appType": "1",
    "curr": "rub",
    "dest": WB_DEST,
    "spp": "30",
}
