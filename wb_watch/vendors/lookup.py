"""Look up Russian legal-entity vendors (director, founders, registration details)
via companies.rbc.ru — RBC's public company registry aggregator (EGRUL-sourced).

Honest scope: only legal entities (ООО, АО, etc.) carry a director + founders
list. Sole proprietors (ИП) *are* the named individual already captured in
`sellers.name` — there's no separate "founder" to resolve for them, so this module
doesn't attempt IP lookups. Chinese cross-border sellers have no RU registry
presence at all.

Superseded an earlier list-org.com-based implementation: list-org started
307-redirecting to a bot-challenge page after a handful of requests, and later a
full connection timeout — a hard IP-level block from that one round of testing.
companies.rbc.ru serves the same category of data (in some respects richer — status,
registration date, share capital) with no anti-bot friction observed across
repeated requests. Two requests per INN: the search page alone already carries
director/address/status/registration date; founders require a second fetch of the
company detail page.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from .. import http

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

_SEARCH_CARD_RE = re.compile(
    r'<a class="company-name-highlight" href="(https://companies\.rbc\.ru/id/[^"]+)">'
    r'<span title="([^"]+)">',
)
# The URL itself embeds the OGRN as its leading path segment (.../id/{ogrn}-slug/).
_OGRN_FROM_URL_RE = re.compile(r"/id/(\d+)-")
_STATUS_RE = re.compile(r'company-status-badge[^"]*">([^<]+)</span>')
_DIRECTOR_RE = re.compile(r'<span>([^<]*[Дд]иректор[^<]*):</span>([^<]+)</p>')
_ADDRESS_RE = re.compile(r'<span>Юридический адрес:</span>([^<]+)</p>')
_REG_DATE_RE = re.compile(r'<span>Дата регистрации:</span>([^<]+)</p>')

_FOUNDERS_SECTION_START = ">Учредители</h2>"
_FOUNDER_NAME_RE = re.compile(r'href="/id/[^"]+"[^>]*>([^<]+)</a>')
_FOUNDER_INN_RE = re.compile(
    r'ИНН</div><div class="company-detail-block__item-inner-container[^"]*">'
    r'<span class="copy-text">(\d+)'
)


def _get(url: str, params: dict[str, str] | None = None) -> str | None:
    http.vendor_throttle_wait()
    try:
        resp = httpx.get(url, params=params, headers=_HEADERS, timeout=15,
                          follow_redirects=True)
    except httpx.TransportError:
        return None
    if resp.status_code != 200:
        return None
    return resp.text


def _parse_search_result(html: str) -> dict[str, Any] | None:
    m = _SEARCH_CARD_RE.search(html)
    if not m:
        return None
    url, name = m.group(1), m.group(2)
    tail = html[m.end():m.end() + 1500]

    director_match = _DIRECTOR_RE.search(tail)
    address_match = _ADDRESS_RE.search(tail)
    reg_date_match = _REG_DATE_RE.search(tail)
    status_match = _STATUS_RE.search(html[:m.start()])

    return {
        "name": name,
        "rbc_url": url,
        "status": status_match.group(1).strip() if status_match else None,
        "director_role": director_match.group(1).strip() if director_match else None,
        "director_name": director_match.group(2).strip() if director_match else None,
        "address": address_match.group(1).strip() if address_match else None,
        "founding_date": reg_date_match.group(1).strip() if reg_date_match else None,
    }


def _parse_founders(html: str) -> list[dict[str, Any]]:
    start = html.find(_FOUNDERS_SECTION_START)
    if start < 0:
        return []
    end = html.find("company-detail-block__title", start + len(_FOUNDERS_SECTION_START))
    section = html[start:end] if end > 0 else html[start:start + 4000]

    names = _FOUNDER_NAME_RE.findall(section)
    inns = _FOUNDER_INN_RE.findall(section)
    # Both lists are emitted in the same founder order; INN may be absent for some
    # (individual founders without a public INN on file) so pair positionally up to
    # the shorter list rather than zip-truncating silently past a real mismatch.
    return [
        {"name": name.strip(), "inn": (inns[i] if i < len(inns) else None)}
        for i, name in enumerate(names)
    ]


def lookup_vendor(inn: str) -> dict[str, Any] | None:
    """Resolve one INN to director/founders/registration details, or None if not
    found on companies.rbc.ru."""
    search_html = _get("https://companies.rbc.ru/search/", params={"query": inn})
    if not search_html:
        return None
    result = _parse_search_result(search_html)
    if not result:
        return None

    detail_html = _get(result["rbc_url"])
    founders = _parse_founders(detail_html) if detail_html else []

    ogrn_match = _OGRN_FROM_URL_RE.search(result["rbc_url"])

    return {
        "inn": inn,
        "ogrn": ogrn_match.group(1) if ogrn_match else None,
        "founding_date": result.get("founding_date"),
        "director_name": result.get("director_name"),
        "director_role": result.get("director_role"),
        "founders": founders,
        "address": result.get("address"),
        "phones": [],
        "email": None,
        "source_url": result["rbc_url"],
    }
