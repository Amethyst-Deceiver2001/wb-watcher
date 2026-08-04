"""Extract text overlaid on product gallery images via local OCR.

Sellers frequently bake promotional text into gallery photos — a soldier in
combat gear captioned "ВЫБОР НАШИХ ГЕРОЕВ" ("the choice of our heroes"), a
banner reading "ДЛЯ СВО" — that never appears in the product title/description
and so is invisible to categorize.py/military_class.py, which only see text
fields. OCR-ing the images and running the same combat-context regex used on
review text (analysis/signals.py) recovers that signal.

Runs entirely locally (tesseract via pytesseract) — no external API calls, no
per-image cost, so this is safe to run at corpus scale unlike a vision-LLM
call per image would be.
"""
from __future__ import annotations

import io

import pytesseract
from PIL import Image

from .signals import detect_field_use_image


def extract_text(image_bytes: bytes) -> str:
    """OCR one image. Returns '' on any decode/OCR failure — a corrupt or
    unreadable image shouldn't abort the caller's loop over a gallery."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # psm 11 ("sparse text", no layout assumption) beats the default psm 3
        # on these images empirically — product photos mix short overlaid
        # captions with background clutter, not the uniform document layout
        # psm 3 assumes; confirmed side by side, psm 11 recovered strictly
        # more text on every test image including the one meant to catch
        # ("ВЫБОР НАШИХ ГЕРОЕВ" over a soldier photo, missed entirely at psm 3).
        text = pytesseract.image_to_string(
            img, lang="rus+eng", config="--psm 11"
        )
        return text.strip()
    except Exception:
        return ""


def analyze_image(image_bytes: bytes) -> dict[str, str | None]:
    """OCR one image and check the extracted text for combat-context signal
    (same regex/phrase-snippet logic as reviews.field_use_signal)."""
    text = extract_text(image_bytes)
    phrase = detect_field_use_image(text) if text else None
    return {"ocr_text": text or None, "field_use_phrase": phrase}
