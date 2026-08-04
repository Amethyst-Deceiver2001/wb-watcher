"""Fetch full description + characteristics from the basket CDN card.json.

The live card.wb.ru endpoint omits the description and spec table; those live in the
static basket JSON. Returns normalized fields ready to merge into the item row.
"""
from __future__ import annotations

from typing import Any

from .. import http
from . import basket


def _flatten_options(data: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize the characteristics table into [{name, value}, ...]."""
    out: list[dict[str, str]] = []
    for group in data.get("grouped_options") or []:
        for opt in group.get("options") or []:
            out.append({"name": opt.get("name", ""), "value": opt.get("value", "")})
    if not out:
        for opt in data.get("options") or []:
            out.append({"name": opt.get("name", ""), "value": opt.get("value", "")})
    return out


def fetch_details(nm_id: int) -> dict[str, Any] | None:
    host = basket.resolve_host(nm_id)
    if host is None:
        return None
    data = http.get_json(basket.card_json_url(nm_id, host))
    if not data:
        return None
    return {
        "description": data.get("description"),
        "characteristics": _flatten_options(data),
        "certificate": data.get("certificate"),
        "vendor_code": data.get("vendor_code"),
        "subj_name": data.get("subj_name"),
        "wb_create_date": data.get("create_date"),
        "imt_id": data.get("imt_id"),
    }
