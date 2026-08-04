"""Tag each item with a broad category so demand (Telegram mentions) and supply
(WB listings/sellers/reviews) can be compared across categories — e.g. munitions
components (unambiguously military, no civilian use) vs medical supplies (dual-use,
also bought for camping/first aid).

Rule order matters: earlier, more specific patterns win over later, broader ones
(a "система сброса" FPV drop rig should land in drone_drop_system, not the generic
fpv_drone bucket it would also match).

Nothing gets discarded. An earlier version denylisted vitamins/supplements/energy
drinks as "noise" pulled in by the similar-items graph — wrong call: Telegram
fundraising posts explicitly request tea, coffee, instant soups, honey, lard,
vitamins alongside medical and drone gear (confirmed live, e.g. the slavamaxuta
post) — nutrition/ration supply is a real, analyzable demand category, not noise.
Only a small, genuinely ambiguous residual goes to `uncertain` (flagged for human
review, not asserted as irrelevant).
"""
from __future__ import annotations

import re

from .normalize import normalize

# (category, pattern) — first match wins.
_RULES: list[tuple[str, re.Pattern]] = [
    ("munitions_component", re.compile(
        r"вог[\s.-]*(17|25)|хвостовик.*вог|накольник.*вог|вог.*хвостовик|"
        r"вог.*накольник", re.I)),
    ("drone_drop_system", re.compile(
        r"сброс.*(fpv|дрон|груз)|система.*сброс|сбросник|airdrop|аirdrop|"
        # "сбрасывание"/"сбрасывания" is a distinct derivational stem from
        # "сброс" (diverges past letter 4) — same class of stem-mismatch
        # bug as баллистик/баллистический; found via image-OCR corroboration
        r"сбрасыва.{0,10}воздух|воздух.{0,10}сбрасыва", re.I)),
    # FPV-specific component brands sold bare, without the word "fpv" or
    # "дрон" anywhere in the title (e.g. "Аккумулятор LiPo Vant 22000мАч",
    # "Антенна GEPRC SOMA 1.3G для дронов") — these brands make FPV-drone
    # parts exclusively, so the bare token is unambiguous.
    ("fpv_drone", re.compile(
        r"fpv|квадрокоптер|\bкоптер|\bдрон|"
        # "оптоволокон" (adjective stem, "оптоволоконная катушка") and
        # "оптоволокно" (bare noun, "катушка оптоволокно") are NOT substrings
        # of each other despite looking related — a regex bug that meant the
        # far more common bare-noun listings never matched at all.
        r"оптоволок[а-я]*.*катушк|катушк.*оптоволок[а-я]*|"
        # "оптоволокно"/"оптоволоконный" alone collides with fiber-optic gun
        # sights on spearfishing reels ("Мотовило для буйрепа ... оптоволокно
        # ... для Glock") — an unrelated domain — so require БПЛА/drone
        # co-occurrence, or the NCZOBOE brand (fiber-optic-drone-link maker,
        # checked full corpus: every match is a genuine drone transceiver).
        r"оптоволок[а-я]*.{0,20}(бпла|дрон)|(бпла|дрон).{0,20}оптоволок[а-я]*|"
        r"nczoboe|"
        r"geprc|peakfpv|betafpv|caddx|hqprop|gemfan|walksnail|rushfpv|axisflying|"
        r"alientech|"
        r"expresslrs|\belrs\b|\bvant\b.*\d+c\b|\d+c\b.*\bvant\b|\bxt60\b|"
        # bare "lipo"/"липо" and FPV patch-antenna brand names — found
        # sitting uncategorized despite zero collision risk (checked full
        # corpus: every current match is genuine FPV drone battery/antenna
        # gear, titles just don't repeat the word "fpv"/"дрон" themselves)
        r"\blipo\b|липо|\brhcp\b|\blhcp\b|pagoda|lumenier|\baxii\b|"
        # bare "патч-антенна"/"антенна патч" — checked full corpus, every
        # match (Maple Wireless, Foxeer Echo, RUSH video receivers) is
        # genuine FPV video-link gear, none carry rhcp/lhcp/brand words
        r"патч.*антенн|антенн.*патч|"
        # DJI/Mavic — the most common consumer drone brand; many accessory
        # listings (chargers, propellers, batteries, motors) name only the
        # brand/model, not "дрон"/"квадрокоптер"/"fpv" — checked full
        # corpus (90 matches), zero unrelated collision
        r"\bdji\b|\bmavic\b|\bмавик\b|"
        # RF amplifiers scoped to the 2.4/5.8 GHz FPV control+video bands.
        # Bare "усилитель" is exactly what made the old "alientech duo
        # усилитель" keyword pull in car-audio amps — the band qualifier is
        # what separates a drone control-link booster from those.
        r"усилител.{0,30}[25][ .,]?[48]\s?ггц|[25][ .,]?[48]\s?ггц.{0,30}усилител|"
        r"наземн.{0,3}станц.{0,3}управлен|\bнсу\b",
        re.I)),
    ("combat_helmet", re.compile(
        r"шлем.*(военн|тактическ|ратник|кивер|альтин|алтын|баллистич|штурмов|"
        r"бр[-\s]?[1-6]\b|свмпэ)|каска.*(военн|тактич|баллистич)|"
        r"свмпэ.*шлем|"
        # Reversed order ("тактический шлем", "баллистических шлемов") wasn't
        # caught by the modifier-after-шлем pattern above — found on helmet
        # accessories (suspension systems, visors) named that way. "бронешлем"
        # (armor-helmet, one fused word) is unambiguous on its own.
        r"(военн|тактическ|баллистич|штурмов).*шлем|\bбронешлем|"
        r"(подвесн|визор|забрал).*(шлем|бронешлем)|"
        r"(шлем|бронешлем).*(подвесн|визор|забрал)",
        re.I)),
    # Kids' dress-up costumes ("детский игровой бронежилет") use the same word
    # as real armor but have no ballistic function — check before body_armor so
    # they don't get counted as real armor-carrier supply.
    ("toy_costume", re.compile(
        r"(детск|для\s+дете[ий]|игров).*бронежилет|"
        r"бронежилет.*(детск|для\s+дете[ий]|игров)",
        re.I)),
    ("body_armor", re.compile(
        r"бронежилет|плитник|\bбронеплит|"
        # "пакет" added to the carrier-noun list: "Защита затылка с
        # противоосколочным пакетом" (a neck/nape armor-panel insert) was
        # falling through to 'other' because "пакет" (ballistic-panel
        # insert, standard armor terminology) wasn't in this co-occurrence
        # list at all — found sitting uncategorized alongside 270+ other
        # armor-accessory items during a full "other"-bucket audit.
        r"противоосколочн.*(комплект|жилет|напашник|бронеж|одеял|покрывал|пакет)|"
        r"(комплект|жилет|напашник|бронеж|одеял|покрывал|пакет).*противоосколочн|"
        # Bare armor-carrier accessory nouns — checked full corpus (289
        # "напашник", 393 "пятиточечник", 109 "КАП", 44 "защита затылка"
        # listings): every single instance across all four is genuine
        # armor-carrier hardware (groin flap, five-point harness, side/
        # front plate insert, nape guard) EXCEPT the "поджопник"/"сидушка"
        # (seat-pad/seat-cushion) variant of пятиточечник/напашник, which
        # is a plain foam seat cushion with no ballistic insert — a
        # functionally different, non-armored product this project's
        # military_class axis already treats separately (see the
        # "поджопник" comment under tactical_wear below). Excluded here so
        # it falls through to that existing tactical_wear rule instead of
        # being forced into armor.
        r"^(?!.*(поджопник|сидушк)).*(напашник|пятиточечник)|\bкап\b|защита затылка|"
        # "жилет для бронепластин"/"Жилет для бронепластин М2" — a plate
        # carrier described as a vest *for* armor plates rather than using
        # the word "бронежилет" itself; found sitting uncategorized in
        # `other` despite being real armor-carrier supply.
        r"жилет.*бронеплит|бронеплит.*жилет|"
        r"жилет.*бронепластин|бронепластин.*жилет|"
        # "разгрузочный жилет"/"жилет разгрузочный" (load-bearing/plate-
        # carrier vest) and bare English "plate carrier" — checked against
        # the full corpus, every current match is genuine tactical/armor
        # gear, no civilian (e.g. fishing) vest collision found.
        r"разгрузочный жилет|жилет разгрузочный|"
        r"\bplate carrier\b|"
        # bare "баллистика"/"комплект баллистики" — standalone soft-armor
        # insert listings (не "бронежилет" wording at all); checked against
        # the full corpus, every one of 60+ current matches is genuine armor
        # gear, no false-positive collision found.
        # broadened from "баллистик" to "баллист": "баллистический" is a
        # separate derivational stem from "баллистика"/"баллистик" (diverges
        # at letter 9: -тик- vs -тич-), not a substring of it — same class
        # of stem bug as оптоволокно/оптоволоконный found earlier
        r"\bбаллист[а-я]*\b|напашник.*баллист|баллист.*напашник|"
        # widened from {0,15}: "Абдоминальный модуль по классу защиты Бр2"
        # puts 20+ chars between the adjective and "защит" — the tighter
        # window was missing genuine abdominal-armor-module listings.
        r"абдоминальн[а-я]*.{0,30}(панел|защит)|защита живота|"
        # "Барьер"/"Барьер ПРО" branded limb/neck/shoulder ballistic covers
        # ("Защита плеча противоосколочная 'Барьер ПРО' Бр2", "Чехол защиты
        # бедра 'Барьер'", "Комплект чехлов защиты конечностей") and the
        # "Панцирь" soft-armor line ("Панцирь 3.2 ССО Бр2") — found sitting
        # in `other` (subj_name "Бронеодежда"/"Защита тела", ~150 listings)
        # because the armor-carrier co-occurrence list above didn't include
        # limb/neck body-part nouns at all, only garment nouns (жилет/
        # напашник/etc). Checked full corpus: every match is genuine
        # ballistic-cover hardware, no civilian collision (the seat-cushion
        # пятиточечник/напашник exclusion above is a separate pattern and
        # still applies).
        r"защит.{0,20}(плеч|голен|бедр|шею\b|шеи\b|предплечь|конечност|затылк)|"
        r"(плеч|голен|бедр|шею\b|шеи\b|предплечь|конечност|затылк).{0,20}защит|"
        r"панцирь.{0,20}сс[оo]|сс[оo].{0,20}панцирь|"
        r"наплечник.{0,20}тактическ|тактическ.{0,20}наплечник|"
        r"тактическ.{0,15}воротник|воротник.{0,15}тактическ|"
        # bare "напашник" + "защита паха" (groin protector), and standalone
        # active-hearing-protection headsets for shooting — found via a
        # v18 catalog-preset sample (56k-item "тактическое снаряжение"
        # preset), checked full corpus, clean
        r"защит.{0,10}паха|напашник.{0,20}защит|"
        r"наушник.{0,15}(активн|стрельб)|"
        # "КАП" (ballistic side-plate insert) sold specifically "для
        # камербанда" (for a plate carrier's side cummerbund) — found while
        # reviewing the "камербанд" discovery sweep: "камербанд" alone is a
        # menswear-tuxedo-waistband/tactical-cummerbund homograph collision
        # (correctly split by other rules), but this КАП+камербанд pairing
        # was falling through both axes entirely with neither existing КАП
        # rule (scoped to "пояс разгрузочный") nor "камербанд" alone catching
        # it. Checked full corpus: 10 items, all genuine armor-plate listings.
        r"\bкап\b.{0,25}камербанд|камербанд.{0,25}\bкап\b",
        re.I)),
    # Checked before tactical_wear: "Саперный крюк кошка тактическая в
    # подсумке" was landing in tactical_wear because "подсумке" contains
    # "сумк", colliding with tactical_wear's "тактическ...сумк" pattern —
    # same substring-collision bug class as elsewhere in this file. Grapple
    # hooks marketed as "саперная кошка" (sapper's grapple) are sold for
    # mine-clearance/route-checking (dragging suspect ground from a safe
    # distance) as much as camping — real combat-engineering demand, not noise.
    ("sapper_gear", re.compile(
        r"крюк.?кошка|саперн.*кошк|кошк.*саперн|подсумок.*саперн|"
        r"крюк.*саперн|саперн.*крюк|стропа эвакуационн", re.I)),
    ("tactical_wear", re.compile(
        # Bare "подсумок" (nominative case, the form used in most titles)
        # doesn't contain the substring "сумк" that the тактическ+пояс/сумк
        # rule below keys off — only oblique-case forms ("подсумка",
        # "подсумке") do. Found via a full "other"-bucket audit: 512 pouch
        # listings sat uncategorized because of this, 200 of them already
        # confirmed strict_military. Checked full corpus (1002 "подсумок"
        # listings): every one is a genuine tactical/weapon-accessory pouch
        # (magazine, grenade, medical, multitool), zero collision — EXCEPT
        # tourniquet/IFAK carrier pouches ("Подсумок под турникет, жгут"),
        # radio holster pouches ("Подсумок для рации"), and flashlight
        # holster pouches ("Подсумок ... для фонарика") — this project
        # already categorizes those as `medical`/`comms_ew`/`lighting`
        # respectively (the item the pouch is FOR, not a generic pouch),
        # and those rules are checked AFTER this one in list order, so
        # they'd never be reached without this exclusion — caught via a
        # full-corpus diff before shipping.
        r"^(?!.*(турникет|жгут|аптечк|рац[а-я]*\b|фонарик)).*подсумок|"
        # Weapon slings and AK/RPK quick-reload loops — no civilian
        # counterpart; found sitting in "other" without the "тактическ"
        # qualifier the existing ремень/тактическ rule below requires.
        # Checked full corpus (18 + 2 listings), zero collision.
        r"ремень оружейн|петля быстрой перезарядк|"
        r"балаклав|тактическ.*(военн|штурмов)|плащ.?палатк|берц|"
        # ВКПО ("Военный Костюм Полевого Обмундирования") is the standard-issue
        # Russian Army field uniform system — scoped to actual garment words so
        # it doesn't also grab "спальный мешок ВКПО" (a sleeping bag, correctly
        # its own field_comfort category, checked below).
        r"(бушлат|костюм|китель|куртка|брюки|форма|футболк|кепк|кепи|пилотк|берет|"
        r"бейсболк).*вкпо|"
        r"вкпо.*(бушлат|костюм|китель|куртка|брюки|форма|футболк|кепк|кепи|пилотк|"
        r"берет|бейсболк)|"
        r"уставн.*(кепк|кепи|пилотк|берет|бейсболк)|"
        r"(кепк|кепи|пилотк|берет|бейсболк).*уставн|"
        r"тактическ.*жилет|жилет.*тактическ|airsoft.*жилет|жилет.*airsoft|"
        r"страйк[а-я]*.*жилет|жилет.*страйк[а-я]*|"
        r"разгрузк[а-я]*.{0,30}тактическ|тактическ.{0,30}разгрузк[а-я]*|"
        r"сумка поясная утилитарн|\bmolle\b|"
        r"тактическ.{0,15}(пояс|сумк)|(пояс|сумк).{0,15}тактическ|"
        r"пояс.{0,10}разгрузочн.{0,20}\bкап\b|\bкап\b.{0,20}пояс.{0,10}разгрузочн|"
        r"армейск.{0,15}плащ|плащ.{0,15}армейск|"
        r"рюкзак.{0,20}(airsoft|страйк[а-я]*)|(airsoft|страйк[а-я]*).{0,20}рюкзак|"
        # Bare military-headwear co-occurrence — "кепка Росгвардии", "Военная
        # камуфляжная кепка", "фуражка военная уставная", "фуражка полиции
        # уставная" weren't caught: only "уставн" paired with кепк/etc. above,
        # and "фуражка" wasn't in the word list at all.
        r"(кепк|кепи|пилотк|берет|бейсболк|фуражк).*"
        r"(военн|тактическ|росгвард|полици|полиц\b)|"
        r"(военн|тактическ|росгвард|полици|полиц\b).*"
        r"(кепк|кепи|пилотк|берет|бейсболк|фуражк)|"
        # ОМОН/МВД bare, and camo-colorway co-occurrence ("кепка мох",
        # "кепка камуфлированная СССР") — found sitting uncategorized;
        # checked full corpus, zero collision with civilian headwear
        r"(кепк|кепи|пилотк|берет|бейсболк|фуражк).*"
        r"(омон|мвд|мох\b|мультикам|цифра|камуфл)|"
        r"(омон|мвд|мох\b|мультикам|цифра|камуфл).*"
        r"(кепк|кепи|пилотк|берет|бейсболк|фуражк)|"
        # bare "маскхалат" and "костюм"/"куртка"/"бушлат" paired with
        # тактическ/камуфляж/горка/маскхалат/acu (not just ВКПО as above) —
        # found via Telegram-corroborated front-line wishlist posts
        # ("маскхалаты", "тактический костюм горка") explicitly requesting
        # these; checked full corpus, zero collision with civilian костюм
        # (business suit / pajama set) listings
        r"маскхалат|"
        r"(костюм|куртка|бушлат).*(тактическ|камуфляж|горка|acu)|"
        r"(тактическ|камуфляж|горка|acu).*(костюм|куртка|бушлат)|"
        # tactical backpacks/boots/sneakers not paired with "берц" or
        # "военн"/"штурмов" — same corroboration
        r"рюкзак.*тактическ|тактическ.*рюкзак|"
        # Army duffels/assault packs not paired with "тактическ" — "Баул
        # армейский 'Витязь'", "Баул рюкзак военный ВКПО", "Рюкзак штурмовой
        # 20 л (кордура мультикам)" — found sitting in `other` (subj_name
        # "Рюкзаки", 22 listings) because the existing рюкзак+тактическ
        # pattern above requires the literal word "тактический", which none
        # of these use. Checked full corpus, zero collision with civilian
        # travel/school backpacks (those don't pair with армейск/военн/
        # штурмов or the кордура/мультикам fabric-name vocabulary).
        r"(рюкзак|баул).{0,20}(штурмов|армейск|военн)|"
        r"(штурмов|армейск|военн).{0,20}(рюкзак|баул)|"
        # Drop-leg/thigh mounting platform for holsters/pouches — worn as
        # combat gear but not itself an armor component (see body_armor's
        # comment on the same "Атаман" product line above).
        r"набедренн.{0,15}платформ|платформ.{0,15}набедренн|"
        r"(ботинк|кроссовк).*тактическ|тактическ.*(ботинк|кроссовк)|"
        # tactical weapon sling / multitool — corroborated in the same
        # front-line wishlist posts
        r"ремень.*тактическ|тактическ.*ремень|"
        r"мультитул.*(армейск|тактическ)|(армейск|тактическ).*мультитул|"
        # tactical knife/hat/buff — found sitting in "other" despite real
        # Telegram-mention corroboration (11-19 mentions each); checked full
        # corpus, zero collision with unrelated knife/hat listings
        r"нож[а-я]*.{0,20}тактическ|тактическ.{0,20}нож[а-я]*|"
        r"нож[а-я]*.{0,20}охотнич|охотнич.{0,20}нож[а-я]*|"
        r"шапк.{0,20}тактическ|тактическ.{0,20}шапк|"
        r"бафф.{0,15}тактическ|тактическ.{0,15}бафф|"
        # bare "поджопник" (belt-worn tactical seat pad) — category axis
        # only, no military_class equivalent: thin/ambiguous SVO signal,
        # equally common in hunting/airsoft camo-market listings
        r"поджопник",
        re.I)),
    ("anti_drone_gear", re.compile(
        r"антидрон|защит.*тепловизор|детектор.*(дрон|бпла)|(дрон|бпла).*детектор|"
        r"обнаружитель.*(дрон|бпла)|3mx|"
        r"булат.{0,15}\bv?\.?\s?[34]\b|\bv?\.?\s?[34]\b.{0,15}булат|"
        r"марс.{0,20}(антенн|док-станц|аккумулятор|таир|держатель|зарядн|"
        r"бпла|детектор|булат)|"
        r"(антенн|док-станц|аккумулятор|таир|держатель|зарядн|бпла|детектор|"
        r"булат).{0,20}марс|"
        # "VIGI" — same fix as military_class.py's drone_detector, a
        # ПВО unit's improvised drone-surveillance camera rig
        r"\bvigi\b|asel labs|асель лабс",
        re.I)),
    ("night_vision", re.compile(
        r"прибор ночного видения|ночного видения|\bпнв\b|night\s?vision", re.I)),
    # Stem "тепловиз" (not just "тепловизор") so adjective forms match too —
    # "тепловизионная камера" (thermal camera module) shares no substring
    # with the noun "тепловизор" otherwise.
    # "arkon"/"arma" bare: Arkon and Arkon Arma are thermal-scope product
    # lines whose mount/adapter/lens-cap accessories (sold as separate
    # listings) don't repeat the word "тепловизор" themselves — checked
    # full corpus, every match is a genuine scope accessory, zero collision
    # [aа]rk[oо]n: some listings mix a Cyrillic А into "Arkon" — and since
    # normalize() (see normalize.py) then homoglyph-converts every OTHER
    # Latin letter in that same word to its Cyrillic lookalike (a purely-
    # Latin word is left alone, but a mixed one isn't), the "o" ends up
    # Cyrillic too post-normalization. Match both forms of each letter.
    ("thermal_optics", re.compile(r"тепловиз|монокуляр|[aа]rk[oо]n|\barma\b", re.I)),
    ("medical", re.compile(
        r"жгут|турникет|бинт|аптечк|шприц|кровоостанав|перевязочн|дрессинг|"
        # combat-trauma supplies found via Telegram-corroborated front-line
        # medical wishlists (chest seals, NPAs, IV gear, tactical litters) —
        # checked full corpus, every match is a genuine trauma/medical item
        r"марл[яе]|носилк|космопор|cosmopor|окклюзионн.{0,3}пластыр|противоожогов|"
        r"щипц[а-я]*.{0,10}кусачк|кусачк[а-я]*.{0,10}костн|"
        r"транексам|торакальн.{0,5}катетер|катетер.{0,5}троакар|"
        r"ворот[а-я]* шанца|игл[а-я]* спинальн|спинальн.{0,5}игл|"
        r"тампонад|воздуховод|назофарингеальн|зажим москит|"
        r"катетер внутривен|капельниц|костыл|натрия хлорид|"
        r"бланк.{0,15}первой помощи|патологоанатомическ|"
        r"ножницы.{0,10}медицинск|воскопран|"
        # Adult diapers/incontinence pads for wounded/bedridden hospital
        # patients — confirmed via t.me/NASHIM37/26150, a unit's thank-you
        # post for a donation delivered straight to a hospital ward. Distinct
        # from baby diapers (kg-weight sizing, "новорожденных", Ultra
        # Comfort/Elite Soft brands) — that cluster stays untouched, this is
        # the "для взрослых" wording specifically.
        r"подгузник[а-я]*.{0,20}взросл|взросл.{0,20}подгузник[а-я]*|"
        r"тупоконечн|термоодеял|изотермическ.{0,10}(одеял|покрывал)|"
        # front-line hospital reanimation-ward supply list (t.me/Vmeste71/8399)
        # — zero tracked items under any of these terms
        r"мини[\s-]?спайк|эпидуральн|модулен|нутризон|энтеральн|"
        r"венозн.{0,10}катетер|катетер.{0,10}венозн|"
        r"краник.{0,10}трехход.{0,10}медицинск|"
        r"трехход.{0,10}краник.{0,10}медицинск|"
        # waterless "dry shower" gel for bedridden/field use — confirmed via
        # wildberries.ru/catalog/839241594, whose own description explicitly
        # says "Полевые условия и СВО ... в блиндажах, палатках" and whose
        # spec table lists "сухой душ армейский" — a genuinely dual-use
        # hospital/field product, not generic marketing language
        r"сухой душ|для лежач.{0,15}(полев|сво)|"
        # bare tactical trauma-shears/ampoule-case, found sitting in "other"
        # despite the identical phrasing already matching elsewhere in this
        # corpus via other co-occurring terms — checked full corpus, clean
        r"ножниц.{0,15}тактическ|тактическ.{0,15}ножниц|ампульниц|"
        r"очки.{0,10}маск.{0,10}тактическ|маск.{0,10}очки.{0,10}тактическ",
        re.I)),
    # Chevron/patch merch ("шеврон РЭБ", "нашивка РАЦИЯ РЭБ") prints the same
    # words as real equipment listings — checked before comms_ew so it doesn't
    # fall through to the equipment bucket.
    ("military_merch", re.compile(
        # "рэб" (acronym) and spelled-out "радиоэлектронн[ая]я борьб[аы]" are
        # not substrings of each other; "подарок" (bare noun) and "подарочн"
        # (adjective stem) likewise diverge — same class of stem/expansion
        # bug as баллистик/гемостатик found earlier this session.
        r"(шеврон|нашивк|значок|жетон|брелок|кружк|наклейк|стикер|флаг|"
        r"открытк|ночник|футболк|сувенир|подарочн|подарок|стопка|петлиц|"
        r"обложк|чехол на паспорт|набор|кепк)"
        r".*(рэб|радио\s*электронн.{0,20}борьб|(?<![а-яё])раци[еёиюя]|(?<![а-яё])раций)|"
        r"(рэб|радио\s*электронн.{0,20}борьб|(?<![а-яё])раци[еёиюя]|(?<![а-яё])раций)"
        r".*(шеврон|нашивк|значок|жетон|брелок|кружк|наклейк|"
        r"стикер|флаг|открытк|ночник|футболк|сувенир|подарочн|подарок|стопка|"
        r"петлиц|обложк|чехол на паспорт|набор|кепк)|"
        # Same merch-word list, but scoped to SVO/patriotic/military-unit
        # symbolism instead of just РЭБ — the RЭБ-only scoping above missed
        # ~1,000 patches found sitting in "other" during a full-bucket
        # audit: Z/V-symbol patches, military-district patches ("Западный
        # военный округ"), Victory Day patches ("Родина-Мать зовёт",
        # Георгиевская лента), Soviet-nostalgia patches (серп и молот,
        # "Рожденный в СССР"), and dark-humor military patches. Scoped to
        # the same carrier-word list, not bare "z"/"войск"/etc. alone, so it
        # can't swallow unrelated flag/mug/sticker merch that happens to
        # mention Russia in a non-military context.
        r"(шеврон|нашивк|значок|жетон|брелок|наклейк|стикер|флаг|патч|"
        r"петлиц|эмблем|худи)"
        r".*(\bz\b|\bv\b|\bсво\b|войск|военн.{0,10}округ|\bвкс\b|"
        r"триколор|георгиевск|побед|родина.?мать|армейск|ссср|"
        r"серп и молот|вагнер|чвк)|"
        r"(\bz\b|\bv\b|\bсво\b|войск|военн.{0,10}округ|\bвкс\b|"
        r"триколор|георгиевск|побед|родина.?мать|армейск|ссср|"
        r"серп и молот|вагнер|чвк)"
        r".*(шеврон|нашивк|значок|жетон|брелок|наклейк|стикер|флаг|патч|"
        r"петлиц|эмблем|худи)",
        re.I)),
    # Bare "рэб" alone catches merch (mugs, flags printed with the acronym)
    # pulled in via similar-items — require it co-occur with an actual
    # equipment/system word or a channel/frequency spec (e.g. "10-канальный
    # (350-6300 мГц)"), not just the initialism on its own.
    # "рация" bare misses declined forms ("рации", "рацию", "раций") since
    # they don't share the full substring — but a bare stem "раци" would
    # wrongly swallow "рацион"/"рационализация" (unrelated: food ration,
    # not radio), so the char class explicitly excludes that "о" continuation.
    # Negative lookbehind guards against a WORSE collision found via a raw
    # search-goods.wildberries.ru probe: "раци[еёиюя]"/"раций" is a substring
    # of ANY word ending "-рация"/"-рации" — фильтрация, операция, декорация,
    # регенерация, вибрация all matched "радио" (comms_ew) bare, e.g. "Мелкое
    # сито мешок для процеживания для фильтрации" wrongly hit comms_ew.
    ("comms_ew", re.compile(
        r"(?<![а-яё])раци[еёиюя]|(?<![а-яё])раций|радиостанц|\btyt\b|\bdmr\b|starlink|старлинк|"
        r"спутниковой связ|спутников.{0,10}интернет|"
        # cellular signal booster explicitly marketed for dugout/bunker use,
        # not just home/apartment — wildberries.ru/catalog/866024695
        r"усилител.{0,15}сигнал.{0,40}(блиндаж|землянк)|"
        r"(блиндаж|землянк).{0,40}усилител.{0,15}сигнал|"
        r"(комплекс|станци|систем|подавител|антенна|рюкзачн).*рэб|"
        r"рэб.*(комплекс|станци|систем|подавител|рюкзачн|\d+[\s-]?канал)|"
        r"п[\s-]?274[\s-]?м\d?|та-?57\b|таи-?43\b|полевой связи|"
        r"полевого телефона|"
        # A rugged "защищенный телефон" alone is also a real civilian
        # construction/outdoor product line — scoped to explicit army-
        # service phrasing so it doesn't swallow that generic market.
        r"телефон.*(службы? в армию|для армии|военнослужащ)|"
        r"(службы? в армию|для армии|военнослужащ).*телефон",
        re.I)),
    ("power_energy", re.compile(
        r"power[\s\-]?bank|павербанк|генератор|зарядн.*станц|электростанц|"
        r"внешн.*аккумулятор|"
        # diesel/portable heaters explicitly marketed for dugout use, not
        # just generic camping — wildberries.ru/catalog/598437797
        r"отопител.{0,40}блиндаж|блиндаж.{0,40}отопител|"
        r"обогреватель.{0,40}блиндаж|блиндаж.{0,40}обогреватель",
        re.I)),
    ("lighting", re.compile(r"фонар", re.I)),
    ("nutrition_rations", re.compile(
        r"витамин|\bбад[ыа]?\b|энергетик|сублимат|сухпаек|сухой\s?паек|тушенк|"
        r"паштет консерв|провизия|рацион питания|\bпаек\b|"
        r"суп.*быстрого приготовлен|лапша.*быстрого приготовлен", re.I)),
    ("field_comfort", re.compile(
        r"спальн.*мешок|спальник|носк|термобель|термокомплект|футболк|перчатк|"
        # tick/mosquito repellent, field water purification, self-heating
        # warmers — corroborated via Telegram front-line wishlist posts
        r"репеллент|антиклещ|обеззаражива.{0,15}вод|хлорка в таблетк|"
        r"самонагрева.{0,5}грелк|грелк.{0,5}самонагрева|"
        # folding field cot — corroborated via the same drop-ship channel
        # shopping list ("Раскладушки")
        r"раскладушк|"
        # Tactical/sniper sleeping mats and shooting mats ("Каремат складной
        # тактический 'Лабиринт'", "Коврик тактический 'Дозор'", "Коврик для
        # стрельбы 'Скат'") — found sitting in `other` (subj_name "Коврики
        # туристические", 37 listings) because this rule only matched the
        # bare "спальн.*мешок"/"спальник" wording, not коврик/каремат.
        # Scoped to тактическ/стрельб so it doesn't sweep in the far larger
        # generic camping-foam-mat market (checked full corpus: every match
        # is genuine tactical-branded gear, e.g. "Каремат снайпера").
        r"(коврик|каремат).{0,20}(тактическ|стрельб|снайпер)|"
        r"(тактическ|стрельб|снайпер).{0,20}(коврик|каремат)",
        re.I)),
    ("construction_tools", re.compile(
        r"гвозд|монтажн.*пен|шпаклевк|саморез|розетк|автомат.*выключател|"
        r"бензопил|масло.{0,10}(цепн|пильн)|(цепн|пильн).{0,10}масло|"
        r"petg|филамент|3d.*принтер|герметик|набор инструмент|держател.*провод|"
        # foil wall/floor insulation — corroborated via a front-line
        # wishlist explicitly requesting it for "перекрытия блиндажей"
        # (covering dugouts): "Рулоны пленок... Утеплитель"
        r"фольгированный утеплитель|утеплитель.{0,10}(стен|пол)|"
        # entrenching shovels and extension cords/power strips — confirmed
        # via a front-line drop-ship channel's live shopping list
        # ("Лопаты", "Удлинитель 10 м") explicitly needed at the front
        r"лопата (штыков|совков)|лопат[аы] сапер|"
        r"удлинитель.{0,10}(сетев|заземл)|сетевой фильтр",
        re.I)),
    ("camo_netting", re.compile(
        r"маскировочн.*сет|затеняющ.*сеть|сеть.*затеняющ|"
        r"камуфляжн.*сеть|сеть.*камуфляжн|маскирующ.*сет|сет.*маскирующ",
        re.I)),
    # Bulk personal-care/hygiene goods — confirmed via a direct front-line
    # unit wishlist post explicitly requesting "Трусы... Носки... Средства
    # личной гигиены... Влажные салфетки" alongside tactical/medical gear.
    # Deliberately scoped narrower than a bare consumer-cosmetics match:
    # "шампунь"/"гель для душа" bare would sweep in ~160 unrelated women's/
    # specialty-cosmetics SKUs (color-treated-hair shampoo, anti-hair-loss
    # treatments) that have nothing to do with bulk soldier care packages —
    # scoped to "мужской" bulk variants instead, checked against the full
    # corpus (30 matches, all genuine men's bulk toiletries).
    ("hygiene_supplies", re.compile(
        r"\bтрусы\b|влажны[ех] салфетк|туалетн.{0,3}бумаг|\bмыло\b|"
        r"зубн.{0,3}(паст|щетк)|"
        r"(шампун|гель для душа).{0,20}мужск|мужск.{0,20}(шампун|гель для душа)|"
        r"дезодорант|бритв|станк.{0,5}брит|пена для брит|гель для брит|"
        r"крем для брит|бальзам после брит|"
        r"стиральн.{0,3}порошок|капсул.{0,5}стирк|полотенц|"
        r"дезинфицирующ|спиртов.{0,3}салфетк|"
        # gear/uniform odor neutralizer explicitly branded/marketed for
        # military equipment care — wildberries.ru/catalog/8193649
        # ("Helmetex Army ... бронежилет и берцы ... каска военная")
        r"helmetex",
        re.I)),
]

# Small residual of items with no plausible field/logistics application found so
# far (pet-care consumables). Flagged for manual review, not deleted or asserted
# irrelevant — e.g. pet absorbent pads have documented battlefield improvised-use
# as cheap wound-dressing filler, so even this bucket may turn out relevant.
# Confirmed zero-signal similar-items-graph drift (checked against
# item_mentions: 0 Telegram corroboration, no plausible field/logistics
# application) — unlike diapers/shampoo/hygiene items, which the project
# deliberately does NOT denylist here (see module docstring: real fundraising
# posts do request those alongside military gear, so a blanket flag would
# repeat a documented past mistake). Galaxy Watch smartwatches and civilian
# appliance antennas (TV, iron, home Wi-Fi) have no such corroborating signal
# at all in the corpus checked.
_UNCERTAIN = re.compile(
    r"пелен.*(собак|кош)|корм для животных|"
    r"galaxy watch|"
    r"антенна.*(wi-?fi|телевизор|утюг|радиоприемник)|"
    r"(wi-?fi|телевизор|утюг|радиоприемник).*антенна",
    re.I,
)


def categorize_item(name: str | None, subj_name: str | None = None) -> str:
    text = normalize(f"{name or ''} {subj_name or ''}")
    for category, pattern in _RULES:
        if pattern.search(text):
            return category
    if _UNCERTAIN.search(text):
        return "uncertain"
    return "other"
