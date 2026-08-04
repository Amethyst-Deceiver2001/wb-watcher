"""Resolve the basket-NN CDN host that serves a given nm-id's full card JSON.

WB shards static product data across basket-01..basket-NN hosts by `vol` (nm // 100000).
The mapping is a set of published vol-ranges; we use it directly, then fall back to
probing hosts when a vol falls outside the known table (WB adds hosts over time).
Resolved hosts are cached per-vol for the process lifetime.

Verified at build time: nm 390000854 -> vol 3900 -> basket-22.
"""
from __future__ import annotations

from .. import config, http

# (max_vol_inclusive, host). First row whose bound covers vol wins.
# Ranges per the community-maintained WB basket table; extended by probing.
_VOL_TABLE: list[tuple[int, int]] = [
    (143, 1), (287, 2), (431, 3), (719, 4), (1007, 5), (1061, 6), (1115, 7),
    (1169, 8), (1313, 9), (1601, 10), (1655, 11), (1919, 12), (2045, 13),
    (2189, 14), (2405, 15), (2621, 16), (2837, 17), (3053, 18), (3269, 19),
    (3485, 20), (3701, 21), (3917, 22), (4133, 23), (4349, 24), (4565, 25),
    (4877, 26), (5189, 27), (5501, 28), (5813, 29), (6125, 30), (6437, 31),
    (6749, 32), (7061, 33), (7373, 34),
]

_PROBE_RANGE = range(1, 41)  # fallback probe span
_cache: dict[int, int] = {}


def vol_part(nm_id: int) -> tuple[int, int]:
    return nm_id // 100000, nm_id // 1000


def _host_from_table(vol: int) -> int | None:
    for bound, host in _VOL_TABLE:
        if vol <= bound:
            return host
    return None


def card_json_url(nm_id: int, host: int) -> str:
    vol, part = vol_part(nm_id)
    return config.BASKET_CARD_URL.format(host=host, vol=vol, part=part, nm=nm_id)


def resolve_host(nm_id: int) -> int | None:
    """Return the basket host number serving this nm-id, or None if unreachable."""
    vol, _ = vol_part(nm_id)
    if vol in _cache:
        return _cache[vol]

    candidate = _host_from_table(vol)
    if candidate is not None and http.head_ok(card_json_url(nm_id, candidate)):
        _cache[vol] = candidate
        return candidate

    # Table miss or stale: probe. Start near the table guess to minimize requests.
    order = sorted(_PROBE_RANGE, key=lambda h: abs(h - (candidate or 1)))
    for host in order:
        if host == candidate:
            continue
        if http.head_ok(card_json_url(nm_id, host)):
            _cache[vol] = host
            return host
    return None
