"""Detect explicit front-line/combat-use confirmation in review text.

Found by manual inspection of the archived review corpus: buyers routinely state
outright, in public WB reviews, that an item is headed to "СВО" (the war) or "фронт"
(the front) — e.g. "Жгуты купила на СВО по просьбе наших защитников", "Очень помогает
в боевой работе". This is materially stronger evidence than category inference (a
tourniquet *might* be for camping; a review saying "отправила на фронт" is not
ambiguous) — so it's promoted to a stored, queryable signal rather than an ad hoc scan.

High recall over precision, same philosophy as tg/extract.py: flag for a human to
read the captured context, don't try to be a perfect classifier.
"""
from __future__ import annotations

import re

# The "ШТУРМ РЕШЕНИЕ" sleeping-bag brand name (seller's own watermark, tiled
# across every infographic image) gets OCR'd with other layout text
# interleaved between the two words — e.g. "ШТУРМ ЭФФЕКТИВНО РЕШЕНИЕ", or
# with the words reversed — so a simple "(?!...)" lookahead only catches one
# of the four variants actually seen (found while fact-checking the
# docs/index.html gallery caption for nm 229951388, which had wrongly cited
# this watermark as combat-use evidence). _first_match special-cases this
# pattern: a match only counts if "решение" does NOT also appear nearby.
_STURM_RE = re.compile(r"\bштурм", re.I)
_STURM_BRAND_NEARBY_RE = re.compile(r"решени", re.I)
_STURM_BRAND_WINDOW = 40


def _is_sturm_brand_watermark(text: str, match: re.Match) -> bool:
    lo = max(0, match.start() - _STURM_BRAND_WINDOW)
    hi = match.end() + _STURM_BRAND_WINDOW
    window = text[lo:hi]
    return bool(_STURM_BRAND_NEARBY_RE.search(window))


# "Центр СБО" (an unrelated commercial certification body, center-cbo.ru — see
# the cniitochmash-sbo-false-lead project memory) gets OCR'd as "Центр сво" on
# the ЦНИИТОЧМАШ ballistic-testing certificate that recurs across 126+ A&P
# Групп/TOGA UNIT image hits — Б/В confusion in this specific document's
# small-print applicant field. Confirmed by direct image inspection (not OCR)
# on two independent SKUs (nm 303794266, nm 212781443) — both read "Центр
# СБО", not "Центр СВО".
#
# Two page types in this document family both name the customer, and OCR
# mangles the preceding word differently on each, so a single fixed prefix
# (e.g. "центр") doesn't cover both:
#   - "ПРОТОКОЛ ИСПЫТАНИЙ" (test protocol) page: "Заказчик" garbles past
#     recognition ("2. Bazan: 000 «Центр сво».") but "ЦНИИТОЧМАШ" survives
#     largely intact ("AO ЦИНИТОЧМАШ").
#   - "СЕРТИФИКАТ СООТВЕТСТВИЯ" (certificate of conformity) page: "Центр"
#     truncates to "Це" ("«Це СВО» место mont") but "ЗАЯВИТЕЛЬ"/"СЕРТИФИКА-"
#     survive intact — 246 hits corpus-wide, found on a broader sweep after
#     the narrower "центр"-prefix check below only caught 4.
# So this document family is recognized by its own stable anchor vocabulary
# (which survives OCR) rather than by what precedes the "сво" match, which
# doesn't. A real "СВО" mention essentially never co-occurs with
# "точмаш"/"заявитель"/"сертифика" in the same OCR blob — every observed
# instance in this corpus is this same boilerplate.
_SVO_RE = re.compile(r"\bсво\b", re.I)
_CENTR_NEARBY_RE = re.compile(r"\bцентр", re.I)
_CENTR_WINDOW = 15
_CERT_DOC_ANCHOR_RE = re.compile(r"точмаш|заявитель|сертифика", re.I)


def _is_centr_sbo_misread(text: str, match: re.Match) -> bool:
    if _CERT_DOC_ANCHOR_RE.search(text):
        return True
    lo = max(0, match.start() - _CENTR_WINDOW)
    return bool(_CENTR_NEARBY_RE.search(text[lo:match.start()]))


# Three more non-combat "позиция" senses found on the same 2026-08-02 sweep,
# none catchable by the (?!он) lookahead above since they use the genuine
# noun form ("позициях"/"позиция"/"позиций") — only distinguishable by
# surrounding context, same approach as _is_sturm_brand_watermark. All three
# are stock/boilerplate phrases repeated verbatim across many SKUs/pages:
#   - MOLLE-mount description: "Размещение на тактическом снаряжении любого
#     типа, в различных позициях" — "positions" = attachment points, not
#     combat positions.
#   - WB's own seller-policy disclaimer: "Это официальная позиция
#     маркетплейса" — "position" = corporate stance, unrelated to combat.
#   - Catalog-size marketing blurb: "Ассортимент — более 10 000 позиций" —
#     "позиций" = SKU count (a retail sense of the word), not battle
#     positions.
_POZITSII_RE = re.compile(r"\bпозици(?!он)", re.I)
_POZITSII_FALSE_POSITIVE_RE = re.compile(
    r"любого\s+типа|маркетплейс|ассортимент", re.I
)
_POZITSII_WINDOW = 40


def _is_pozitsii_false_positive(text: str, match: re.Match) -> bool:
    lo = max(0, match.start() - _POZITSII_WINDOW)
    hi = min(len(text), match.end() + _POZITSII_WINDOW)
    return bool(_POZITSII_FALSE_POSITIVE_RE.search(text[lo:hi]))


# Word-boundaried where the token is short/ambiguous (сво overlaps "свой"/"своих"
# as a substring); root-matched where the stem is long enough to be unambiguous.
_PATTERNS: list[re.Pattern] = [
    _SVO_RE,
    # Leading \b on every stem below: without it these collide with common
    # marketing/product vocabulary — "фронт" inside "конФРОНТация", "окоп"
    # inside "высОКОПрочная", "ранени" inside "хРАНЕНИе", "позици" inside
    # "комПОЗИЦИя"/"эксПОЗИЦИя" — a real false-positive source surfaced once
    # OCR text (noisy marketing copy) started flowing through this detector,
    # not just clean review text where these collisions never came up.
    # (?!альн) excludes "фронтальный/-ая/-ое" (e.g. sleeping-bag "фронтальная
    # панель" = front panel) — a civilian adjective sharing the stem with the
    # noun "фронт" (the war front) that this pattern is meant to catch.
    # (?!\s*\+) excludes car-audio amplifier spec sheets, which label their
    # channel-selector switches "Фронт+Тыл" / "Фронт+Саб" (Front+Rear,
    # Front+Sub speaker channels) — found producing a false image-signal hit
    # on a car amplifier listing ("класс АВ", "честный RMS" elsewhere on the
    # same infographic). "фронт" is never legitimately followed by "+" in
    # war-context OCR/review text, so this exclusion is safe generally
    # rather than special-cased to "тыл" alone.
    re.compile(r"\bфронт(?!альн)(?!\s*\+)", re.I),
    re.compile(r"\bокоп", re.I),
    _STURM_RE,
    # (?!ой стиль) excludes the "БОЕВОЙ СТИЛЬ" brand/watermark (a seller's own
    # name, OCR'd off every product photo) — found producing 5 false
    # image-signal hits with zero actual field-use confirmation content.
    re.compile(r"\bбоев(?!ой стиль)", re.I),
    re.compile(r"\bбойц", re.I),
    re.compile(r"\bэвакуа", re.I),
    re.compile(r"\bранени", re.I),
    # (?!он) excludes both the "позиционировать/-руется/-рует/-рование" verb
    # family (GPS/device "positioning", "marketed as" — zero combat-position
    # meaning) AND the "N-позиционный/-ая/-ое" adjective family (multi-
    # position chargers/switches, e.g. "зарядное устройство 6-ти
    # позиционное" — a distinct false-positive class found on a corpus-wide
    # phrase-frequency sweep 2026-08-02, same non-combat "position" sense).
    # Originally only excluded "(?!онир)"; widened to "(?!он)" since no
    # genuine combat-position noun form ("позиция"/"позиции"/"позиций")
    # is ever followed by "он" — that syllable only occurs in this false-
    # positive family.
    _POZITSII_RE,
    # "ЛБС" (линия боевого соприкосновения — line of combat contact) and "БЗ"
    # (боевой выход/боевая задача — combat deployment/mission) are common
    # soldier-slang acronyms in review text ("на лбс", "заходил на бз") —
    # both short/ambiguous enough to need strict word boundaries.
    re.compile(r"\bлбс\b", re.I),
    re.compile(r"\bбз\b", re.I),
    re.compile(r"на\s+передов", re.I),
]

# "герой/героев/героям" is common patriotic-marketing phrasing on product
# images ("выбор наших героев") — but in review text it's mostly colloquial
# ("для моего героя" = my boyfriend, "город героя Челябинск" = the official
# "Hero City" honorific) with no war connection at all. Kept separate from
# _PATTERNS (used for reviews) and only applied to OCR'd image text, where
# the marketing-phrase reading is actually the common case.
_IMAGE_ONLY_PATTERNS: list[re.Pattern] = [re.compile(r"\bгеро", re.I)]

_CONTEXT_BEFORE = 20
_CONTEXT_AFTER = 40


def _first_match(text: str, patterns: list[re.Pattern]) -> str | None:
    for pat in patterns:
        for m in pat.finditer(text):
            if pat is _STURM_RE and _is_sturm_brand_watermark(text, m):
                continue
            if pat is _SVO_RE and _is_centr_sbo_misread(text, m):
                continue
            if pat is _POZITSII_RE and _is_pozitsii_false_positive(text, m):
                continue
            start = max(0, m.start() - _CONTEXT_BEFORE)
            end = min(len(text), m.end() + _CONTEXT_AFTER)
            # Expand both edges outward to the nearest word boundary (all the
            # way to the string edge if the cut word runs off it) so the
            # snippet never opens/closes mid-word — a fixed 20/40-char window
            # otherwise regularly turns e.g. "боеспособность" into
            # "тоспособность" or "заказывала" into "казывала".
            while start > 0 and not text[start - 1].isspace():
                start -= 1
            while end < len(text) and not text[end].isspace():
                end += 1
            return text[start:end].strip()
    return None


def detect_field_use(text: str | None) -> str | None:
    """Return a short context snippet around the first match, or None."""
    if not text:
        return None
    return _first_match(text, _PATTERNS)


def detect_field_use_image(text: str | None) -> str | None:
    """Same as detect_field_use, plus marketing-only phrasing ("герой") that
    would be too noisy to apply to review text."""
    if not text:
        return None
    return _first_match(text, _PATTERNS) or _first_match(text, _IMAGE_ONLY_PATTERNS)
