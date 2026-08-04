"""Extract structured intel from Telegram post text.

Pulls the things that matter for linking a fundraising post to marketplace listings
and to the people running it:
  - Wildberries nm-ids (from wildberries.ru / wb.ru links)
  - Ozon product references
  - all URLs
  - Russian delivery addresses (heuristic, keyed on address markers)
  - phone numbers
  - payment/donation details: card numbers, SBP (СБП) phone-transfers, bank names

These are regex heuristics over messy user text; they aim for high recall (flag for
a human) rather than perfect precision.
"""
from __future__ import annotations

import re
from typing import Any

# --- marketplace ids ---
_WB_LINK = re.compile(r"(?:wildberries\.ru/catalog/|wb\.ru/catalog/)(\d+)", re.I)
_WB_NM_QUERY = re.compile(r"[?&]nm=(\d+)", re.I)
_OZON_LINK = re.compile(r"ozon\.ru/(?:product|t)/([\w\-]+)", re.I)
_URL = re.compile(r"https?://[^\s)>\]]+", re.I)
# Any wildberries.ru/wb.ru URL verbatim, even ones our nm-id regex can't parse
# (shortened links, non-catalog paths, size-only query strings, etc.) — kept
# separately so discovery never silently drops a WB reference.
_WB_ANY = re.compile(r"https?://(?:www\.)?(?:wildberries\.ru|wb\.ru)/\S+", re.I)
# Textual channel references — t.me/<name> links or bare @handle mentions — used to
# build the "extended network" of channels worth following up on, independent of
# Telethon's structured forward metadata (captured separately in scraper.py).
_TG_CHANNEL_LINK = re.compile(r"t\.me/(?!joinchat|\+)([A-Za-z0-9_]{4,32})", re.I)
_TG_HANDLE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{3,31})\b")

# --- contact / logistics ---
_PHONE = re.compile(r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
# Card numbers: 16 digits, optionally space/dash grouped in 4s.
_CARD = re.compile(r"\b(?:\d[ \-]?){15}\d\b")

# Address markers (Russian): city/street/oblast abbreviations.
_ADDR_MARKERS = re.compile(
    r"(?:\bг\.?\s|\bгород\b|\bул\.?\s|\bулица\b|\bпр-?кт\b|\bпроспект\b|\bд\.?\s?\d|"
    r"\bкрай\b|\bобласть\b|\bпос\.?\b|\bиндекс\b|\bпочтовый\b)",
    re.I,
)
_BANKS = re.compile(
    r"\b(Сбер(?:банк)?|Тинькофф|Т-?Банк|Альфа(?:-?банк)?|ВТБ|Озон\s?банк|"
    r"Райффайзен|Газпромбанк|Совкомбанк|ЮMoney|Юмани|Qiwi|Киви|СБП)\b",
    re.I,
)


def _dedupe(seq: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for s in seq:
        seen.setdefault(s.strip(), None)
    return [s for s in seen if s]


def extract_wb_nm_ids(text: str) -> list[int]:
    ids = _WB_LINK.findall(text or "") + _WB_NM_QUERY.findall(text or "")
    return sorted({int(x) for x in ids})


def extract(text: str) -> dict[str, Any]:
    text = text or ""
    urls = _dedupe(_URL.findall(text))
    wb_ids = extract_wb_nm_ids(text)
    wb_links = _dedupe(_WB_ANY.findall(text))
    ozon_ids = _dedupe(_OZON_LINK.findall(text))
    ref_channels = _dedupe(
        [h.lower() for h in _TG_CHANNEL_LINK.findall(text)]
        + [h.lower() for h in _TG_HANDLE.findall(text)]
    )

    # Addresses: keep lines that contain an address marker.
    addresses = [
        ln.strip()
        for ln in text.splitlines()
        if _ADDR_MARKERS.search(ln) and len(ln.strip()) > 8
    ]

    cards = _dedupe(m.group(0) for m in _CARD.finditer(text))
    banks = _dedupe(m.group(0) for m in _BANKS.finditer(text))
    phones = _dedupe(_PHONE.findall(text))

    payment_details: list[str] = []
    payment_details += [f"card:{c}" for c in cards]
    payment_details += [f"bank:{b}" for b in banks]

    return {
        "links": urls,
        "wb_nm_ids": wb_ids,
        "wb_links": wb_links,
        "ozon_ids": ozon_ids,
        "referenced_channels": ref_channels,
        "addresses": _dedupe(addresses),
        "phones": phones,
        "payment_details": payment_details,
    }
