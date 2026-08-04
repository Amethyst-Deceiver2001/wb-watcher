"""Resolve Ozon short links to a product id + descriptive slug text.

Honest scope: Ozon's product pages and search/composer APIs are behind an anti-bot
JS challenge (confirmed: plain HTTP GETs to any www.ozon.ru/product/... or the
composer-api/entrypoint-api endpoints return a 307/redirect-to-challenge, never real
content) — unlike Wildberries' fully open internal JSON API, there is no equivalent
price/seller/reviews access for Ozon via a plain HTTP client. What *is* open: Ozon's
short-link redirector (ozon.ru/t/{code}) resolves with a plain nginx 301 that is not
behind the challenge, and the Location header it returns is a slugified product URL
that embeds both the numeric product id and a readable, hyphenated description —
e.g. ozon.ru/t/1Gu1YVO -> .../product/antidronovyy-plashch-poncho-antidronovoe-
nakidka-ot-teplovizora-oksford-tsvet-moh-2476704614/. That gives category
classification and stable product identity for free, even though price/seller/
reviews remain out of reach.

Telegram posts have been observed citing Ozon short links under both /t/{code} and
/product/{code} paths — but /product/{code} always hits the same bot challenge as a
full product page (confirmed live), so it's not attempted at all; trying it just
burns extra requests against the rate limit for a call that never succeeds. Only
/t/{code} is used.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

from .. import http

_SLUG_RE = re.compile(r"/product/([a-z0-9\-]+)-(\d+)/", re.I)


def _parse_location(location: str) -> dict[str, Any] | None:
    parsed = urlparse(location if location.startswith("http") else f"https://x{location}")
    m = _SLUG_RE.search(unquote(parsed.path) + "/")
    if not m:
        return None
    slug_words, product_id = m.group(1), m.group(2)
    return {
        "product_id": product_id,
        "slug_text": slug_words.replace("-", " "),
        "full_url": f"https://www.ozon.ru/product/{slug_words}-{product_id}/",
    }


def resolve_short_code(code: str) -> dict[str, Any] | None:
    location = http.get_redirect_location(f"https://ozon.ru/t/{code}")
    if not location:
        return None
    parsed = _parse_location(location)
    if not parsed:
        return None
    return {**parsed, "short_code": code}
