"""Parse Ozon product/listing/review data out of a user-captured HAR file.

Ozon's product, category, and review APIs (entrypoint-api.bx/page/json/v2,
composer-api.bx/...) are behind a bot-detection wall that blocks plain HTTP
clients outright (see ozon/resolve.py) — confirmed this session: curl/WebFetch
get a 307-into-challenge with an explicit `ozon-antibot: 1` header on every
attempt, including a bare robots.txt fetch. A real authenticated browser
session gets through cleanly with zero cookies attached, which rules out a
simple cookie-replay workaround — the block is almost certainly a TLS/JS
fingerprint check, not a token a script can carry.

So unlike wb/card.py (a single clean JSON endpoint, fetchable directly), Ozon
data collection here works the way the project's WB SVO-tag-page reconnaissance
already does: the user captures a HAR export (DevTools > Network > Export HAR)
from their own browser session, hands it over, and this module parses it
offline — no network calls, so it isn't a "crawling job" under CLAUDE.md and is
safe to run directly rather than being handed back to the user as a script.

Three response shapes are recognized, distinguished by URL pattern and payload:
  - Product page: full server-rendered HTML (`window.__NUXT__.state` blob is
    layout config only; the actual price/seller text is rendered straight
    into the DOM, and full-text characteristics/description come from the
    `entrypoint-api.bx/page/json/v2?url=/product/...` XHR's `widgetStates`).
  - Category/search/brand/seller listing grid: `entrypoint-api.bx/page/json/v2`
    response whose widgetStates include a `tileGridDesktop-*` key — paginated
    item tiles with id/name/price/rating/images, no auth-specific state.
  - Reviews: `entrypoint-api.bx/page/json/v2?url=.../reviews/pdp-part` response
    whose widgetStates include a `webListReviews-*` key.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

_PRODUCT_ID_RE = re.compile(r"-(\d+)/?(?:[?&]|$)")


def product_id_from_url(url: str) -> str | None:
    """Pull the trailing numeric product id off an og:url, a HAR request
    URL's embedded ?url=/product/<slug>-<id>/ param, or a plain product URL."""
    m = _PRODUCT_ID_RE.search(unquote(url))
    return m.group(1) if m else None

_JS_ESCAPE_RE = re.compile(r"\\(u[0-9a-fA-F]{4}|.)")
_JS_ESCAPE_MAP = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}


def _js_unescape(s: str) -> str:
    def repl(m: re.Match) -> str:
        g = m.group(1)
        if g in _JS_ESCAPE_MAP:
            return _JS_ESCAPE_MAP[g]
        if g.startswith("u"):
            return chr(int(g[1:5], 16))
        return g

    return _JS_ESCAPE_RE.sub(repl, s)


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


_PRICE_RE = re.compile(r"([\d\s]{3,9})\s?₽")


def _parse_prices(text: str) -> list[int]:
    """Extract rendered ruble amounts in document order, e.g. from
    'data-widget="webPrice"' DOM text: card price, regular price, then the
    crossed-out original price, when present."""
    out = []
    for m in _PRICE_RE.finditer(text):
        digits = m.group(1).replace(" ", "").replace(" ", "").replace("\xa0", "")
        if digits.isdigit():
            out.append(int(digits))
    return out


def parse_product_page(html: str) -> dict[str, Any]:
    """Extract title/price/seller/og-url from a product page's raw HTML
    (DevTools 'Copy > Copy outerHTML' or a HAR response body both work — this
    only needs the rendered DOM text, not the __NUXT__ state blob)."""
    result: dict[str, Any] = {
        "title": None,
        "price_rub": None,
        "price_original_rub": None,
        "seller_name": None,
        "seller_reg_number": None,
        "og_url": None,
    }

    m = re.search(r'property="og:url"\s+content="([^"]+)"', html)
    if m:
        result["og_url"] = m.group(1)

    # [^<]* (not the more permissive .*?...</div></div> this used to be) stops
    # at the *first* tag boundary — titles are plain text with no nested
    # markup, but on some pages an adjacent sibling <div> (rating/review-count
    # rail right after the heading) sits close enough that a lazy multi-div
    # match swallowed it too, e.g. producing "...VT-6094, мультикам 4.9 •
    # 382 отзыва 26 вопросов" instead of just the title (found on nm
    # 2035705433's HAR capture).
    m = re.search(r'data-widget="webProductHeading"[^>]*>([^<]*)', html)
    if m:
        result["title"] = _strip_tags(m.group(1)) or None
    if not result["title"]:
        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        if m:
            result["title"] = m.group(1).strip() or None

    idx = html.find('data-widget="webPrice"')
    if idx != -1:
        snippet = html[idx:idx + 3000]
        prices = _parse_prices(_strip_tags(snippet))
        if prices:
            # First number is the best available (card/promo) price; the
            # largest number in the block is the pre-discount original price.
            result["price_rub"] = prices[0]
            if len(prices) > 1:
                result["price_original_rub"] = max(prices)

    # Anchor on "about-seller-text" (appears right before the tooltip's own
    # content block) and read forward — "about-seller-tooltip" (the qaId)
    # sits at the *end* of the same block, past the ИП/ООО name and reg
    # number, so anchoring there and reading backward needs a window wider
    # than the block ever reliably is. Confirmed against two real captures
    # (nm 4834581348 "ИП Вторыгина...", nm 2035705433 "ИП Маресьев...").
    idx = html.find("about-seller-text")
    if idx != -1:
        window = html[idx:idx + 800]
        m = re.search(r'"content":"((?:ИП|ООО)[^"]*?)"', window)
        if m:
            result["seller_name"] = _js_unescape(m.group(1))
        m = re.search(r'"content":"(\d{13,15})"', window)
        if m:
            result["seller_reg_number"] = m.group(1)

    return result


def _flatten_widget_states(payload: dict[str, Any]) -> dict[str, Any]:
    """Decode every widgetStates value (each is itself a JSON-encoded
    string) into parsed objects, skipping any that fail to decode."""
    out = {}
    for key, raw in (payload.get("widgetStates") or {}).items():
        try:
            out[key] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def parse_characteristics_and_description(payload: dict[str, Any]) -> dict[str, Any]:
    """From an entrypoint-api.bx/page/json/v2?url=/product/... response:
    pull the webCharacteristics spec table and webDescription rich text."""
    widgets = _flatten_widget_states(payload)
    result: dict[str, Any] = {"characteristics": {}, "description_text": None}

    for key, val in widgets.items():
        if key.startswith("webCharacteristics") and isinstance(val, dict):
            chars = {}
            for group in val.get("characteristics") or []:
                for item in group.get("short") or []:
                    name = item.get("name")
                    values = ", ".join(
                        v.get("text", "") for v in item.get("values") or [] if v.get("text")
                    )
                    if name and values:
                        chars[name] = values
            if chars:
                result["characteristics"] = chars

        if key.startswith("webDescription") and isinstance(val, dict):
            texts = []
            content = (val.get("richAnnotationJson") or {}).get("content") or []
            for block_group in content:
                for block in block_group.get("blocks") or []:
                    for item in (block.get("text") or {}).get("items") or []:
                        if item.get("type") == "text" and item.get("content"):
                            texts.append(item["content"])
            if texts:
                result["description_text"] = "\n".join(texts)

    return result


def parse_listing_grid(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """From a category/search/brand/seller listing response: pull every item
    tile (tileGridDesktop widget) into a flat list of dicts."""
    widgets = _flatten_widget_states(payload)
    items: list[dict[str, Any]] = []

    for key, val in widgets.items():
        if not key.startswith("tileGridDesktop") or not isinstance(val, dict):
            continue
        for tile in val.get("items") or []:
            product_id = tile.get("id")
            if not product_id:
                continue
            name = price_rub = price_original_rub = rating = review_count = None
            for state in tile.get("mainState") or []:
                if state.get("type") == "textDS":
                    text = (state.get("textDS") or {}).get("text", "")
                    if name is None and len(text) > 15 and "осталось" not in text:
                        name = text
                elif state.get("type") == "priceV2":
                    prices = _parse_prices(
                        " ".join(
                            p.get("text", "")
                            for p in (state.get("priceV2") or {}).get("price") or []
                        )
                    )
                    if prices:
                        price_rub = prices[0]
                        if len(prices) > 1:
                            price_original_rub = max(prices)
                elif state.get("type") == "labelListV2":
                    label_items = (state.get("labelListV2") or {}).get("items") or []
                    for i, li in enumerate(label_items):
                        if li.get("type") == "text" and li["text"].get("text"):
                            val_text = li["text"]["text"]
                            if rating is None and re.fullmatch(r"\d\.\d", val_text):
                                rating = float(val_text)
                            elif "отзыв" in val_text:
                                m = re.match(r"([\d\s  ]+)", val_text)
                                if m:
                                    review_count = int(re.sub(r"\s", "", m.group(1)))

            image_urls = [
                img.get("image", {}).get("link")
                for img in (tile.get("tileImage") or {}).get("items") or []
                if img.get("type") == "image" and img.get("image", {}).get("link")
            ]
            link = (tile.get("action") or {}).get("link")

            # tileGridDesktop is reused for on-page "similar/recommended"
            # rails as well as real category/search/seller grids, and that
            # variant's tile sub-schema doesn't carry a >15-char textDS name
            # the same way — skip rather than write a nameless placeholder
            # row that'd otherwise get category='other' from empty text.
            if not name:
                continue

            items.append(
                {
                    "product_id": str(product_id),
                    "name": name,
                    "price_rub": price_rub,
                    "price_original_rub": price_original_rub,
                    "rating": rating,
                    "review_count": review_count,
                    "image_urls": image_urls,
                    "link": f"https://www.ozon.ru{link}" if link and link.startswith("/") else link,
                }
            )

    return items


def parse_reviews(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """From a .../reviews/pdp-part response: pull every review (webListReviews
    widget) into a flat list of dicts, one row per review."""
    widgets = _flatten_widget_states(payload)
    out: list[dict[str, Any]] = []

    for key, val in widgets.items():
        if not key.startswith("webListReviews") or not isinstance(val, dict):
            continue
        product_id = val.get("itemId")
        for rv in val.get("reviews") or []:
            content = rv.get("content") or {}
            author = rv.get("author") or {}
            photos = [p.get("url") for p in content.get("photos") or [] if p.get("url")]
            out.append(
                {
                    "uuid": rv.get("uuid"),
                    "product_id": str(product_id) if product_id else None,
                    "author_name": author.get("firstName") or None,
                    "text": content.get("comment") or None,
                    "score": content.get("score"),
                    "created_at_unix": rv.get("createdAt"),
                    "photo_urls": photos,
                }
            )

    return out


def entry_kind(url: str) -> str | None:
    """Classify a HAR entry's request URL into one of the three shapes this
    module knows how to parse, or None if it's irrelevant (assets, tracking
    beacons, unrelated XHRs)."""
    if "entrypoint-api.bx/page/json/v2" not in url:
        if "/product/" in url and "ozon.ru" in url and "api" not in url:
            return "product_html"
        return None
    if "/reviews/pdp-part" in url or "%2Freviews%2Fpdp-part" in url:
        return "reviews"
    return "listing_or_product_json"
