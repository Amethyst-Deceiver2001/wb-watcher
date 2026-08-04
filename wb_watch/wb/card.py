"""Fetch the live product card: price, seller, rating, stock, feedback count.

Endpoint: card.wb.ru v4. Returns one product per nm-id. Prices are in kopecks.
Splits into a stable `item` dict (identity/seller) and a `snapshot` dict (volatile
price/stock/rating) so the caller upserts identity but appends the time series.
"""
from __future__ import annotations

from typing import Any

from .. import config, http


def fetch_card(nm_id: int) -> dict[str, Any] | None:
    params = {**config.COMMON_PARAMS, "nm": str(nm_id)}
    data = http.get_json(config.CARD_URL, params)
    if not data:
        return None
    products = data.get("products") or []
    if not products:
        return None
    p = products[0]

    # Pick the first size that carries a price (single-size goods use size[0]).
    price_basic = price_product = None
    stocks: list[dict[str, Any]] = []
    for size in p.get("sizes", []):
        price = size.get("price") or {}
        if price and price_basic is None:
            price_basic = price.get("basic")
            price_product = price.get("product")
        for st in size.get("stocks", []):
            stocks.append({
                "wh": st.get("wh"), "qty": st.get("qty"), "dtype": st.get("dtype"),
            })

    item = {
        "nm_id": p.get("id"),
        "imt_id": p.get("root"),
        "name": p.get("name"),
        "brand": p.get("brand"),
        "brand_id": p.get("brandId"),
        "subject_id": p.get("subjectId"),
        "supplier_id": p.get("supplierId"),
        "supplier_name": p.get("supplier"),
        "supplier_rating": p.get("supplierRating"),
        "pics": p.get("pics") or 0,
    }
    snapshot = {
        "nm_id": p.get("id"),
        "price_basic": price_basic,
        "price_product": price_product,
        "total_qty": p.get("totalQuantity"),
        "stocks": stocks,
        "rating": p.get("rating"),
        "review_rating": p.get("reviewRating"),
        "feedback_count": p.get("feedbacks"),
    }
    return {"item": item, "snapshot": snapshot}
