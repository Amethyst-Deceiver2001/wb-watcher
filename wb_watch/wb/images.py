"""Resolve and fetch gallery image bytes for a product card.

WB serves images from the same basket-NN CDN host as the card JSON (see
basket.py), at a predictable path keyed by image index (1-based). The card's
`pics` count (card.py) tells us how many images actually exist — indices
beyond that reliably 404, so callers should cap the loop at `pics`.

Found by manual inspection: seller-uploaded gallery images routinely carry
promotional text overlays ("ВЫБОР НАШИХ ГЕРОЕВ" — "the choice of our heroes",
over a soldier in combat gear) that name the item's actual market — a signal
completely invisible to text-only classification of the product title.
"""
from __future__ import annotations

from .. import http
from . import basket

_SIZE = "c516x688"  # mid-resolution gallery thumbnail; enough for OCR, cheap to fetch

# Exposed as a constant (not just a default arg) so callers deciding whether an
# already-scanned item needs a rescan (e.g. pipeline/track_items.py's
# scan_images, after this cap was bumped from 6) can compare against the same
# number without duplicating it.
DEFAULT_MAX_IMAGES = 12


def image_url(nm_id: int, index: int, host: int) -> str:
    vol, part = basket.vol_part(nm_id)
    return (
        f"https://basket-{host:02d}.wbbasket.ru/vol{vol}/part{part}/"
        f"{nm_id}/images/{_SIZE}/{index}.webp"
    )


def fetch_gallery(
    nm_id: int, pics: int, max_images: int = DEFAULT_MAX_IMAGES
) -> list[tuple[int, bytes]]:
    """Fetch up to `max_images` gallery images (index, bytes), skipping any
    that fail.

    Bumped from 6 to 12: measured against our own corpus, signal-by-index
    doesn't front-load the way the original cap assumed — index 6 still
    carried 40 confirmed hits, no decay from index 1-5's 226/56/88/152/104 —
    and ~40% of tracked items have galleries the old cap was truncating
    outright (spot-checked live: several had 7-13 real photos against a
    6-image cap). OCR is local (pytesseract, no per-image API cost, see
    analysis/ocr.py), so there's no cost case for keeping the cap tight;
    12 covers every gallery size seen in that spot check without going
    fully uncapped."""
    host = basket.resolve_host(nm_id)
    if host is None or pics <= 0:
        return []
    out = []
    for idx in range(1, min(pics, max_images) + 1):
        data = http.get_bytes(image_url(nm_id, idx, host))
        if data:
            out.append((idx, data))
    return out
