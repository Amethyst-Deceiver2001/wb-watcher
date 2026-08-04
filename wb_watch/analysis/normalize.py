"""Homoglyph normalization for classification.

Some listings mix visually-identical Latin letters into otherwise-Cyrillic words
(e.g. "подaвителей" with a Latin U+0061 "a" instead of Cyrillic "а") —
either
deliberate keyword-stuffing to dodge exact-match filters, or just a stray
keyboard-layout slip. Either way it silently defeats every Cyrillic-only regex
in categorize.py/military_class.py.

Translating every Latin letter to its Cyrillic lookalike unconditionally would
be worse than the bug it fixes: plenty of rules match genuine Latin tokens
("TYT", "starlink", "3mx", "fpv", "softair") that must stay untouched. So this
only rewrites Latin letters inside a word that also contains a Cyrillic
letter — a purely-Latin word is left alone.
"""
from __future__ import annotations

import re

_HOMOGLYPHS = str.maketrans({
    "a": "а", "A": "А", "e": "е", "E": "Е", "o": "о", "O": "О",
    "p": "р", "P": "Р", "c": "с", "C": "С", "x": "х", "X": "Х",
    "y": "у", "Y": "У", "B": "В", "H": "Н", "K": "К", "M": "М", "T": "Т",
})

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+")
_HAS_CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def _fix_word(m: re.Match) -> str:
    word = m.group(0)
    if _HAS_CYRILLIC.search(word):
        return word.translate(_HOMOGLYPHS)
    return word


def normalize(text: str) -> str:
    return _WORD_RE.sub(_fix_word, text)
