"""Checks tracked items against Wildberries' own binding seller-contract annex,
"Перечень запрещенных товаров" (List of Prohibited Goods), effective 2026-07-10 —
saved verbatim at config/reference/wb_perechen_zapreshchennykh_tovarov_20260710.pdf
(discovered via a linked reference inside WB's public seller offer-agreement PDF,
static-basket-02.wbbasket.ru/vol20/offers/prd/product/99/...pdf).

This is a third, independent axis from `category` (what kind of item) and
`military_class` (is demand SVO-driven): does the item fall under a category
Wildberries' own contract with sellers explicitly bans from sale on the platform?
Unlike the other two axes, this isn't inferred from title vocabulary alone — each
rule here is grounded in a specific numbered clause of that document, quoted in
CLAUSES below, and only applied where our existing `category`/`military_class`
axes already give high confidence the item genuinely matches the clause's scope
(rather than re-deriving matches from raw regex on noisy titles a second time).

Clauses actually checked (the rest of the ~50-item list — alcohol, pyrotechnics,
vape products, firearms/ammunition proper (a different regulatory domain than
this project's non-weapon-gear focus — see CLAUDE.md), etc. — is out of scope):
  39   "Готовое армейское снаряжение... другие товары военного ассортимента,
        форменное обмундирование..." (ready-made army equipment, other goods of
        military assortment, uniform/regulation clothing)
  2.5/2.10  "любые макеты боеприпасов (патронов, гранат, мин и т.д.)" (mockups of
        ammunition — cartridges, grenades, mines) / composite ammunition parts
  17   "Подавители (глушилки) сигнала GPS/Глонасс/.../Сотовой связи/Сети Wi-Fi"
        (GPS/GLONASS/cellular/Wi-Fi signal jammers)
  16   "Устройства для негласного получения информации, а также шифровальная
        техника" (covert-surveillance devices and cryptographic/encryption
        technology). Legally different in kind from the other three: this is a
        general Russian regulatory category (FSB crypto-licensing under
        existing law — covers commercial VPN hardware, encrypted radios sold to
        anyone) rather than a military-specific ban like §39. Flagged only for
        `comms_ew` items whose own title explicitly advertises encryption
        (e.g. "с шифрованием AES 256") — narrow enough to be defensible on the
        text alone, but the site/brief should keep the distinction explicit:
        "needs a license this seller likely doesn't have," not "this is army-
        only gear." Don't broaden this clause's scope without re-reading that
        caveat.
"""
from __future__ import annotations

import re

CLAUSES: dict[str, dict[str, str]] = {
    "39_army_uniform": {
        "label": "Готовое армейское снаряжение / товары военного ассортимента / форменное обмундирование",
        "quote": (
            "Готовое армейское снаряжение, а также ткани, используемые для его "
            "изготовления, другие товары военного ассортимента, форменное "
            "обмундирование..."
        ),
    },
    "2.5_ammo_mockup": {
        "label": "Макеты боеприпасов (патронов, гранат, мин и т. д.) и составные части боевых патронов",
        "quote": (
            "Запрещена продажа любых макетов боеприпасов (патронов, гранат, мин "
            "и т.д.). Боевые патроны запрещены к продаже не только целиком, но и "
            "в виде составных частей."
        ),
    },
    "17_signal_jammer": {
        "label": "Подавители (глушилки) сигнала GPS/ГЛОНАСС/сотовой связи/Wi-Fi",
        "quote": (
            "Подавители (глушилки) сигнала GPS/Глонасс/Платон/Сотовой связи "
            "(GSM, LTE, пр.)/Сети Wi-Fi."
        ),
    },
    "16_crypto_equipment": {
        "label": "Устройства для негласного получения информации, а также шифровальная техника",
        "quote": (
            "Устройства для негласного получения информации, а также "
            "шифровальная техника."
        ),
    },
}

# Categories (from categorize.py) that, when the item is also `strict_military`
# (no plausible civilian use — see military_class.py), are genuinely "ready-made
# army equipment"/"uniform" rather than merely camo-styled civilian gear. Merch
# (patches, mugs) and functional accessories not themselves "equipment" are
# deliberately excluded — this stays conservative rather than blanket-mapping
# every category to the clause.
_CLAUSE_39_CATEGORIES = {"body_armor", "combat_helmet", "tactical_wear"}

# The comment above already claimed accessories were excluded from
# `tactical_wear`, but no code enforced it — found during a pre-publish
# audit: 202 of 4,272 clause-39 matches were pouches/backpacks/belts/
# gloves/duffels/holsters (подсумок/рюкзак/пояс/ремень/бафф/баул/сумка/
# перчатки/чехол/кобура), which are carrying accessories, not "готовое
# армейское снаряжение... форменное обмундирование" (ready-made army
# EQUIPMENT/uniform CLOTHING) in the sense clause 39 actually names. Full
# garments/headwear/protective gear (костюм, кепка, бушлат, плащ-палатка,
# разгрузка, шлем, бронежилет) stay in scope — only bags/straps/gloves/
# holsters are excluded here.
_CLAUSE_39_ACCESSORY_RE = re.compile(
    # \bпояс (no trailing boundary) to also catch declined forms — "для
    # тактического пояса", "подтяжки для пояса" — missed by an earlier
    # \bпояс\b that only matched the bare nominative form, letting 19 belt-
    # accessory items (strap caps, suspender clips) through on first check.
    r"подсумок|рюкзак|\bпояс|ремень|бафф|баул|сумк|перчатк|чехол|кобур",
    re.I,
)

# munitions_component (categorize.py) is specifically ВОГ-40 grenade tailfins/
# firing pins — literal composite ammunition parts, unconditionally in scope
# regardless of military_class.
_CLAUSE_2_5_CATEGORIES = {"munitions_component"}

# Narrow co-occurrence, not a bare category match: anti_drone_gear/comms_ew
# both contain plenty of detectors/boosters/antennas that are NOT jammers —
# only flag an item whose title says "глушитель"/"подавитель" co-occurring
# with an explicit signal type, mirroring the clause's own wording.
_JAMMER_RE = re.compile(
    r"(глушител|подавител)[а-я]*.{0,20}"
    r"(сигнал|gps|глонасс|gsm|lte|wi-?fi|вай-?фай|сотов|связи)|"
    r"(сигнал|gps|глонасс|gsm|lte|wi-?fi|вай-?фай|сотов|связи).{0,20}"
    r"(глушител|подавител)[а-я]*",
    re.I,
)

# Scoped to comms_ew (radios/field-telephone gear) AND explicit encryption
# wording in the title — "рация ... с шифрованием AES 256" is a precise match
# for clause 16's "шифровальная техника"; a bare "защищенный телефон для
# армии" (rugged, no-camera phone marketed at soldiers, no actual crypto) is
# NOT — that's a different, much larger bucket (~40 items) with zero
# encryption capability, and including it would overclaim this clause's
# actual scope.
_CRYPTO_RE = re.compile(r"шифрован|\baes[\s-]?256\b", re.I)


def classify_wb_policy(
    name: str | None, category: str | None, military_class: str | None
) -> dict[str, str | None]:
    text = name or ""
    if _JAMMER_RE.search(text):
        return {"wb_policy_clause": "17_signal_jammer", "wb_policy_reason": CLAUSES["17_signal_jammer"]["label"]}
    if category == "comms_ew" and _CRYPTO_RE.search(text):
        return {"wb_policy_clause": "16_crypto_equipment", "wb_policy_reason": CLAUSES["16_crypto_equipment"]["label"]}
    if category in _CLAUSE_2_5_CATEGORIES:
        return {"wb_policy_clause": "2.5_ammo_mockup", "wb_policy_reason": CLAUSES["2.5_ammo_mockup"]["label"]}
    if (
        category in _CLAUSE_39_CATEGORIES
        and military_class == "strict_military"
        and not (category == "tactical_wear" and _CLAUSE_39_ACCESSORY_RE.search(text))
    ):
        return {"wb_policy_clause": "39_army_uniform", "wb_policy_reason": CLAUSES["39_army_uniform"]["label"]}
    return {"wb_policy_clause": None, "wb_policy_reason": None}
