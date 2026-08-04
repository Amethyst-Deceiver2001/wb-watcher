"""Discovery: keyword search and seller-catalog enumeration.

Both return lightweight product stubs ({nm_id, name, supplier_id, supplier}); the
tracking pipeline fetches full detail for anything new. Both paginate until an empty
page. The seller-catalog host is especially 429-prone — the shared http layer handles
backoff, but we also stop after a few consecutive empty/failed pages.
"""
from __future__ import annotations

from typing import Any, Iterator

from .. import config, http

_MAX_PAGES = 50


def _stub(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "nm_id": p.get("id"),
        "name": p.get("name"),
        "supplier_id": p.get("supplierId"),
        "supplier": p.get("supplier"),
        "brand": p.get("brand"),
    }


def _products(data: Any) -> list[dict[str, Any]]:
    if not data:
        return []
    # Response shape has varied: sometimes {products:[...]}, sometimes {data:{products}}.
    if isinstance(data.get("data"), dict):
        return data["data"].get("products") or []
    return data.get("products") or []


def _page_size(prods: list[dict[str, Any]]) -> int:
    return max(len(prods), 1)


def keyword_search(query: str) -> Iterator[dict[str, Any]]:
    """Paginate one keyword, stopping at the end of WB's *real* result set.

    Two guards, both added after measuring where discovery noise actually came
    from (see the module note below):

    - `total` bound: WB reports the true result count on page 1. Without it we
      paginated to _MAX_PAGES regardless — e.g. "зарядная станция для
      аккумуляторов дрона" reports total=111 (2 pages) yet had ingested 1746
      tracked items, almost all of it fallback junk.
    - loop detection: past its real depth WB does NOT return an empty page
      (all the old code stopped on) — it re-serves the same page forever.
      Verified: pages 5, 12 and 30 of one query were byte-identical. So a page
      whose ids we have all seen already means we are looping; stop.
    """
    seen: set[int] = set()
    dup_streak = 0
    max_pages = _MAX_PAGES
    page = 0
    # NB: a `for page in range(1, max_pages + 1)` here would be wrong — range()
    # is built once from the initial max_pages, so narrowing it from `total`
    # below would silently have no effect.
    while page < max_pages:
        page += 1
        params = {
            **config.COMMON_PARAMS,
            "query": query,
            "resultset": "catalog",
            "sort": "popular",
            "page": str(page),
        }
        try:
            data = http.get_json(config.SEARCH_URL, params)
        except RuntimeError:
            # Persistent 429 after retries; give up on this keyword gracefully
            # rather than aborting the whole discovery run.
            return
        prods = _products(data)
        if not prods:
            return

        if page == 1:
            total = (data or {}).get("total")
            if isinstance(total, int) and total > 0:
                pages_for_total = -(-total // _page_size(prods))  # ceil
                max_pages = min(max_pages, max(pages_for_total, 1))

        fresh = [p for p in prods if p.get("id") and p["id"] not in seen]
        if not fresh:
            # Every id repeated — almost certainly the fallback loop. Tolerate
            # one such page before giving up, since `sort=popular` can reshuffle
            # between requests and briefly hide the genuinely-new tail.
            dup_streak += 1
            if dup_streak >= 2:
                return
            continue
        dup_streak = 0
        for p in fresh:
            seen.add(p["id"])
            yield _stub(p)


def seller_catalog(supplier_id: int) -> Iterator[dict[str, Any]]:
    empty_streak = 0
    for page in range(1, _MAX_PAGES + 1):
        params = {
            **config.COMMON_PARAMS,
            "supplier": str(supplier_id),
            "sort": "popular",
            "page": str(page),
        }
        try:
            prods = _products(http.get_json(config.SELLER_CATALOG_URL, params))
        except RuntimeError:
            # Persistent 429 after retries; give up on this seller gracefully.
            return
        if not prods:
            empty_streak += 1
            if empty_streak >= 2:
                return
            continue
        empty_streak = 0
        for p in prods:
            if p.get("id"):
                yield _stub(p)
