"""Fetch a seller's legal identity (name, INN, OGRNIP) from the static basket JSON.

This is the accountability layer: it resolves a storefront trademark to the registered
legal entity / sole proprietor behind it. Verified: seller 4032436 -> ИП Карабанов А.И.,
INN 343560018539.
"""
from __future__ import annotations

from typing import Any

from .. import config, http


def fetch_seller(supplier_id: int) -> dict[str, Any] | None:
    url = config.SELLER_INFO_URL.format(sid=supplier_id)
    data = http.get_json(url)
    if not data:
        return None
    return {
        "supplier_id": supplier_id,
        "name": data.get("supplierName"),
        "full_name": data.get("supplierFullName"),
        "inn": data.get("inn"),
        "ogrnip": data.get("ogrnip"),
        "kpp": data.get("kpp"),
        "trademark": data.get("trademark"),
        "supplier_rating": None,  # filled from card data when available
    }
