"""Classify a seller's entity type and supply-chain origin from INN + name.

Russian INN: 10 digits = legal entity (ООО/АО/etc), 12 digits = individual (sole
trader or natural person) — OGRNIP presence confirms sole trader. Chinese sellers on
WB's cross-border program carry an 18-character alphanumeric Unified Social Credit
Code instead of a Russian INN, and/or a CJK-script storefront name — that's a
distinct supply-chain story from domestic sellers (cross-border logistics, different
accountability regime) so it's tagged separately rather than lumped in as "unknown".
"""
from __future__ import annotations

import re

_CJK = re.compile(r"[一-鿿]")
_CN_USCC = re.compile(r"^[0-9A-Z]{18}$", re.I)
_RU_LEGAL_MARKER = re.compile(r"\b(ООО|АО|ЗАО|ПАО|ОКБ|КБ)\b", re.I)
_RU_SOLE_MARKER = re.compile(r"\bИП\b", re.I)


def classify_seller(inn: str | None, name: str | None,
                     ogrnip: str | None = None) -> dict[str, str]:
    inn = (inn or "").strip()
    name = name or ""

    if _CJK.search(name) or (inn and not inn.isdigit() and _CN_USCC.match(inn)):
        return {"entity_type": "cn_business", "origin": "cross_border_cn"}

    if ogrnip or _RU_SOLE_MARKER.search(name) or (inn.isdigit() and len(inn) == 12):
        return {"entity_type": "sole_proprietor_ru", "origin": "domestic_ru"}

    if _RU_LEGAL_MARKER.search(name) or (inn.isdigit() and len(inn) == 10):
        return {"entity_type": "legal_entity_ru", "origin": "domestic_ru"}

    return {"entity_type": "unknown", "origin": "unknown"}
