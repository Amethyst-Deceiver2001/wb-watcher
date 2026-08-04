"""Second-axis classification, orthogonal to `categorize.py`'s functional category:
is this item strictly military-use (no meaningful civilian application), or a
civilian good whose demand is driven by front-line use ("dual-use, high demand")?

Order matters: `_STRICT_RULES` is checked first since some terms would otherwise be
ambiguous against the dual-use list (e.g. a generic "прицел" scope vs. a weapon-
mounted "прицел для АК"). Anything matching neither falls to "other" — most of the
catalog (unrelated goods swept in via similar-items) isn't war-relevant at all, and
that's an expected, not a failure, outcome.
"""
from __future__ import annotations

import re

from .normalize import normalize

# Chevrons/patches print the same words as real gear ("нашивка РАЦИЯ РЭБ") —
# checked before either rule list so merch doesn't get counted as demand.
_MERCH_WORD = (
    r"шеврон|нашивк|значок|жетон|брелок|кружк|наклейк|стикер|флаг|"
    r"открытк|ночник|футболк|сувенир|подарочн|стопка|магнит(?!ный крепеж)"
)
_RADIO_WORD = r"(?<![а-яё])рация"  # guard: bare "рация" is a substring of
# ANY word ending "-рация" (операция, декорация, вибрация, фильтрация,
# регенерация) — found via a raw search-goods.wildberries.ru probe.
_MERCH = re.compile(
    rf"({_MERCH_WORD}).*(рэб|{_RADIO_WORD})|(рэб|{_RADIO_WORD}).*({_MERCH_WORD})",
    re.I,
)

# Airsoft/replica items ("страйкбольная плита", "макет бронеплиты") reuse the
# exact vocabulary of real armor/weapon listings but are inert plastic props —
# checked before _STRICT_RULES so a toy plate doesn't count as armor supply.
#
# "детск"/"для дете[ий]"/"игров" are scoped to co-occur with an actual weapon/
# armor-shaped noun (mirrors categorize.py's toy_costume, which scopes the same
# words to "бронежилет" specifically) — found firing as a BARE, unscoped
# exclusion here, silently zeroing out real dual-use hygiene items like
# "Влажные детские салфетки" (baby wet wipes, corroborated as genuine
# front-line bulk-purchase demand) just for containing the word "детские".
_REPLICA = re.compile(
    # \b on softair/airsoft: bare (unbounded) matched as a substring inside
    # "SINAIRSOFT" (a real plate-carrier brand name), wrongly excluding 3
    # genuine armor-vest listings via this early short-circuit regardless
    # of what _STRICT_RULES would otherwise say
    # "страйкбол" broadened to "страйк[а-я]*" — "страйка"/"страйку" (other
    # declensions of the same word, e.g. "жилет для страйка и охоты") don't
    # share the full "страйкбол" substring and were slipping through
    r"макет|муляж|страйк[а-я]*|\bsoftair\b|\bairsoft\b|игрушечн|"
    r"(детск|для\s+дете[ий]|игров).*"
    r"(шлем|жилет|бронежилет|каска|бронеплит|автомат|пистолет|нож|винтовк|"
    r"подсумок|дрон|квадрокоптер|бпла|мячик)|"
    r"(шлем|жилет|бронежилет|каска|бронеплит|автомат|пистолет|нож|винтовк|"
    r"подсумок|дрон|квадрокоптер|бпла|мячик).*(детск|для\s+дете[ий]|игров)",
    re.I,
)

# Interior Ministry / domestic law enforcement (ОМОН, МВД, полиция,
# Росгвардия — the latter also reports to the Interior Ministry, not the
# military) is a demand driver unrelated to SVO/front-line use. Checked
# before _STRICT_RULES/_DUAL_USE_RULES so a "кепка МВД уставная" or
# "Росгвардии" item doesn't get counted via generic уставн/camo-colorway
# patterns that don't otherwise distinguish it from actual military gear.
# Overridden by a genuine SVO/combat marker co-occurring in the same
# title (e.g. "бронежилет тактический ... для МВД и военных").
_INTERIOR_MINISTRY = re.compile(r"омон|мвд\b|полиц|росгвард", re.I)
# Deliberately excludes "военн"/"тактическ"/"баллист" here — those are used
# loosely on police-branded listings too (e.g. "Кепка Росгвардия... тактическая"
# is still police headwear, not army). Only markers with no plausible police
# use count as an override.
_GENUINE_MILITARY_MARK = re.compile(
    # "вкпо" added when introducing _CIVILIAN_SPORT_HUNT below: a regulation
    # ВКПО uniform sold with an athletic-cut descriptor ("Костюм спортивный
    # военный ВКПО уставной") was being downgraded to dual-use by that
    # check's bare "спортивн" trigger. Safe: "вкпо" has zero collision with
    # police/Interior-Ministry branding in this corpus (checked full corpus).
    # NOTE: don't add "уставн" here — it looked equally safe at a glance but
    # this constant doubles as the Interior-Ministry override condition
    # above (`_INTERIOR_MINISTRY.search and not _GENUINE_MILITARY_MARK`),
    # and "уставн" collides constantly with police branding ("Кепка полиция
    # уставная МВД") — adding it broke that exclusion and let police caps
    # leak into strict_military. Caught by testing before backfill, not
    # after; re-test both directions before adding anything else here.
    r"армейск|для армии|военнослужащ|\bсво\b|штурмов|вкпо", re.I
)

# Same override shape as _INTERIOR_MINISTRY above, for a different collision:
# "тактическ"-branded gear that the seller's own title frames as sport/hunting/
# tourism gear, not army equipment — "Перчатки тактические спортивные для
# тренировок", "нож охотничий тактически туристически", "Рюкзак туристический
# 'Дунай MOLLE'". These are the textbook dual_use_demand case (real civilian
# market riding a "tactical" styling trend), not strict_military, and were
# found sitting in strict_military/military_uniform via the bare
# тактическ+noun co-occurrence rules below, which don't otherwise check for a
# civilian framing word in the same title. Scoped to `military_uniform` and
# `fpv_drone_combat` only (see classify_military) — the two reasons this
# collision was actually observed on (racing/freestyle FPV drones sold
# "спортивный", tactical-styled accessories sold for hunting/tourism/training)
# — not applied blanket, since e.g. an armor plate explicitly for "охоты"
# would be a strange claim worth keeping visible rather than silently
# downgrading.
_CIVILIAN_SPORT_HUNT = re.compile(
    r"спортивн|охотнич|туристич|тренировк|рыбалк", re.I
)
_CIVILIAN_SPORT_HUNT_SCOPED_REASONS = {"military_uniform", "fpv_drone_combat"}

# "Дрон-опрыскиватель для DJI Mini" — a novelty water-sprayer gimmick
# attachment for a ~250g consumer photography drone, swept in only via the
# bare DJI/Mavic brand match. Too small/light to carry any meaningful
# payload; the sprayer framing itself confirms recreational, not combat,
# intent. Checked full corpus: 3 matches, all this same listing.
_DRONE_GIMMICK = re.compile(r"опрыскива", re.I)

# Sapper's grapple hook — checked before _STRICT_RULES so "Саперный крюк
# кошка тактическая в подсумке" doesn't get caught by _STRICT_RULES'
# "тактическ...сумк" pattern first (подсумке contains сумк — a substring
# collision, same bug class as _DRONE_GIMMICK guards against). Mine-clearance/
# route-checking use, not just camping, but still dual-use rather than
# strict-military regardless of "тактическ" co-occurring in the title.
_SAPPER_HOOK = re.compile(
    r"крюк.?кошка|саперн.*кошк|кошк.*саперн|подсумок.*саперн|"
    r"крюк.*саперн|саперн.*крюк", re.I,
)

_STRICT_RULES: list[tuple[str, re.Pattern]] = [
    # Checked before armor_plate so a helmet marked with a БР-class rating (e.g.
    # "шлем военный ... БР2") is labeled combat_helmet, not armor_plate — the bare
    # БР[1-6] pattern below would otherwise win on generic class-number presence
    # alone regardless of what kind of item it's rating.
    ("combat_helmet", re.compile(
        r"шлем.*(военн|тактическ|ратник|кивер|альтин|алтын|баллистич|штурмов|"
        r"бр[-\s]?[1-6]\b|свмпэ)|каска.*(военн|тактич|баллистич)|"
        # Reversed order ("тактический шлем", "баллистических шлемов") missed
        # by the modifier-after-шлем pattern above; "бронешлем" is one fused,
        # unambiguous word on its own — see categorize.py's parallel fix.
        r"(военн|тактическ|баллистич|штурмов).*шлем|\bбронешлем|свмпэ.*шлем|"
        r"(подвесн|визор|забрал).*(шлем|бронешлем)|"
        r"(шлем|бронешлем).*(подвесн|визор|забрал)",
        re.I,
    )),
    # ВКПО ("Военный Костюм Полевого Обмундирования") is the standard-issue
    # Russian Army field uniform system designation — no civilian entity would
    # call a garment this. Scoped to garment words so it doesn't also catch
    # "спальный мешок ВКПО" (a sleeping bag, correctly dual_use via "спальн").
    ("military_uniform", re.compile(
        r"(бушлат|костюм|китель|куртка|брюки|форма|футболк|кепк|кепи|пилотк|берет|"
        r"бейсболк).*вкпо|"
        r"вкпо.*(бушлат|костюм|китель|куртка|брюки|форма|футболк|кепк|кепи|пилотк|"
        r"берет|бейсболк)|"
        r"уставн.*(кепк|кепи|пилотк|берет|бейсболк)|"
        r"(кепк|кепи|пилотк|берет|бейсболк).*уставн|"
        # Bare military-headwear co-occurrence — "кепка Росгвардии", "фуражка
        # военная уставная" weren't caught: only "уставн" paired with кепк/etc.
        # above, and "фуражка" wasn't in the word list at all.
        #
        # Deliberately excludes "полиция"/"полиц"/"омон"/"мвд"/"росгвард"
        # — this axis asks specifically whether demand is SVO/front-line
        # driven, and Росгвардия (National Guard) reports to the Interior
        # Ministry, same domestic-law-enforcement chain as MVD/OMON/police,
        # not an SVO combat-demand driver. These tokens still count toward
        # categorize.py's tactical_wear — that axis is functional type
        # only, not a war-relevance claim.
        r"(кепк|кепи|пилотк|берет|бейсболк|фуражк).*"
        r"(военн|тактическ)|"
        r"(военн|тактическ).*"
        r"(кепк|кепи|пилотк|берет|бейсболк|фуражк)|"
        # camo-colorway co-occurrence ("кепка мох", "кепка камуфлированная
        # СССР") — a genuine field/combat signal distinct from law-
        # enforcement branding, so kept on this axis
        r"(кепк|кепи|пилотк|берет|бейсболк|фуражк).*"
        r"(мох\b|мультикам|цифра|камуфл)|"
        r"(мох\b|мультикам|цифра|камуфл).*"
        r"(кепк|кепи|пилотк|берет|бейсболк|фуражк)|"
        # bare "маскхалат" and костюм/куртка/бушлат paired with
        # тактическ/камуфляж/горка/acu — confirmed via Telegram front-line
        # wishlist posts explicitly requesting "маскхалаты", "тактический
        # костюм горка"; same fix as categorize.py's tactical_wear
        r"маскхалат|"
        r"(костюм|куртка|бушлат).*(тактическ|камуфляж|горка|acu)|"
        r"(тактическ|камуфляж|горка|acu).*(костюм|куртка|бушлат)|"
        r"рюкзак.*тактическ|тактическ.*рюкзак|"
        r"ремень.*тактическ|тактическ.*ремень|"
        # tactical knife/hat/buff — found sitting in "other" despite real
        # Telegram-mention corroboration (11-19 mentions each); checked full
        # corpus, zero collision with unrelated knife/hat listings
        r"нож[а-я]*.{0,20}тактическ|тактическ.{0,20}нож[а-я]*|"
        r"нож[а-я]*.{0,20}охотнич|охотнич.{0,20}нож[а-я]*|"
        r"шапк.{0,20}тактическ|тактическ.{0,20}шапк|"
        r"бафф.{0,15}тактическ|тактическ.{0,15}бафф|"
        # balaclava, gloves, ammo pouches, load-bearing vests paired with
        # тактическ/штурмов/военн/армейск — found via an AK magazine pouch
        # ("Подсумок тактический штурмовой на 3 магазина АК") and assault
        # vest ("Жилет тактический штурмовой") sitting in 'other'; checked
        # full corpus (209 matches), zero collision with police/interior-
        # ministry branding (already globally excluded above regardless)
        r"(балаклав|перчатк|подсумок|\bжилет\b).{0,25}"
        r"(тактическ|штурмов|военн|армейск)|"
        r"(тактическ|штурмов|военн|армейск).{0,25}"
        r"(балаклав|перчатк|подсумок|\bжилет\b)|"
        # "Разгрузка тактическая" (bare chest rig, no "жилет" word) and
        # "Сумка поясная утилитарная 'Лабиринт'" (camo-colorway EDC waist
        # bag: мультикам/мох/пиксель/кордура) — checked full corpus, clean.
        r"разгрузк[а-я]*.{0,30}тактическ|тактическ.{0,30}разгрузк[а-я]*|"
        r"сумка поясная утилитарн|\bmolle\b|"
        r"тактическ.{0,15}(пояс|сумк)|(пояс|сумк).{0,15}тактическ|"
        r"армейск.{0,15}плащ|плащ.{0,15}армейск",
        re.I,
    )),
    # "противоосколочный" (anti-fragmentation) alone also matches shooting
    # glasses/masks (safety eyewear, not armor) — scoped to co-occur with an
    # actual carrier/kit noun so "Очки маска тактические противоосколочные
    # для стрельбы" doesn't false-positive as body armor.
    ("armor_plate", re.compile(
        r"бр[-\s]?[1-6]\b|бронеклас|бронеплит|бронепанел|плитник|"
        r"бронежилет.*баллистик|баллистик.*бронежилет|"
        r"бронежилет.*(плитник|кап[ао]м)|"
        r"тактическ.*бронежилет|бронежилет.*тактическ|"
        r"штурмов.*бронежилет|бронежилет.*штурмов|"
        r"армейск.*бронежилет|бронежилет.*армейск|"
        r"universal armor|full armor|"
        # "пакет" added to the carrier list: "Защита затылка с
        # противоосколочным пакетом" (ballistic-panel insert for a nape
        # guard) was falling through to 'other' on both axes — found via
        # the same full "other"-bucket audit as the categorize.py parallel
        # fix; "пакет" is standard armor-insert terminology, not a generic
        # word, so no collision risk added.
        r"противоосколочн.*(комплект|жилет|напашник|бронеж|плитник|одеял|покрывал|пакет)|"
        r"(комплект|жилет|напашник|бронеж|плитник|одеял|покрывал|пакет).*противоосколочн|"
        # Bare "защита затылка" (nape/neck armor guard) and bare "КАП"
        # (side/front ballistic plate insert) — checked full corpus (44
        # "защита затылка", 109 "КАП" listings): every instance is genuine
        # armor hardware, zero collision, so no co-occurrence scoping
        # needed beyond what's already required for КАП elsewhere in this
        # rule (the bare form here supersedes those narrower alternatives,
        # kept for their explanatory comments).
        r"защита затылка|\bкап\b|"
        # Bare "пятиточечник"/"напашник" (five-point armor harness/groin
        # flap) EXCEPT the "поджопник"/"сидушка" (seat-pad) variant, which
        # is a plain foam seat cushion with no ballistic insert — the
        # textbook dual_use_demand case (also sold to hunters/campers/
        # airsoft players), not strict_military. Mirrors categorize.py's
        # parallel exclusion on the category axis.
        r"^(?!.*(поджопник|сидушк)).*(пятиточечник|напашник)|"
        # bare "напашник...баллист" — categorize.py already has this pair;
        # found missing here via an image-OCR field-use signal on two
        # listings ("с баллистикой") that otherwise fell to 'other' despite
        # 19 near-identical siblings already matching via other co-occurring
        # phrasing. Checked full corpus (21 matches), all genuine armor.
        r"напашник.{0,30}баллист|баллист.{0,30}напашник|"
        # bare "жилет...разгрузочн[ый]" (plate-carrier vest) — found via the
        # same image-OCR pass: 8 of 60 near-identical plate-carrier listings
        # (CPC/T-ARMIS/Yakeda/Панцирь ССО brands, "для бронепластин") sat
        # in 'other' purely for lacking the тактическ/баллист/армейск
        # qualifier the other rules require. Checked full corpus, clean —
        # every match in this cluster is a genuine plate carrier
        r"жилет.{0,15}разгрузочн|разгрузочн.{0,15}жилет|"
        # bare "пояс разгрузочн[ый]...кап" — same product line as the
        # баллистика-qualified belts already matching above, just missing
        # that one word; corroborated by an image reading "БОЕВОЙ стиль
        # ТАКТИЧЕСКОЕ СНАРЯЖЕНИЕ" on the product infographic
        r"пояс.{0,10}разгрузочн.{0,20}\bкап\b|\bкап\b.{0,20}пояс.{0,10}разгрузочн|"
        # bare "КАП...камербанда" (ballistic side-plate insert for a plate
        # carrier's cummerbund) — mirrors categorize.py's fix for the same
        # gap, found reviewing the "камербанд" discovery sweep (10 items
        # falling through both axes; "камербанд" alone is a genuine tuxedo-
        # waistband homograph collision, correctly split by other rules)
        r"\bкап\b.{0,25}камербанд|камербанд.{0,25}\bкап\b|"
        # bare "бронежилет" and "plate carrier" — a body armor vest / plate
        # carrier has no plausible civilian use regardless of qualifiers;
        # military_class required a co-occurring qualifier while
        # categorize.py's body_armor category already bare-matched it,
        # an inconsistency that left plain "Бронежилет" and many Plate-
        # Carrier-branded vests (FCPC, KZ Tactical, TacTec, IDOGEAR,
        # EmersonGear) sitting in military_class='other'
        r"бронежилет|\bplate carrier\b|"
        # widened from {0,15}: "Абдоминальный модуль по классу защиты Бр2"
        # puts 20+ chars between the adjective and "защит" — mirrors the
        # categorize.py fix for the same undercount.
        r"абдоминальн[а-я]*.{0,30}(панел|защит)|защита живота|"
        # bare groin-protector/active-hearing-protection headset — see
        # categorize.py's body_armor for the corroborating source
        r"защит.{0,10}паха|напашник.{0,20}защит|"
        r"наушник.{0,15}(активн|стрельб)|"
        # "Барьер"/"Барьер ПРО" branded limb/neck/shoulder ballistic covers
        # and the "Панцирь ... ССО" soft-armor line — mirrors categorize.py's
        # body_armor fix for the same undercount (~150 listings sitting in
        # `other` on both axes). No civilian dual-use angle: these are
        # ballistic inserts/covers sold as accessories to a plate carrier,
        # not a standalone garment with a hunting/tourism market.
        r"защит.{0,20}(плеч|голен|бедр|шею\b|шеи\b|предплечь|конечност|затылк)|"
        r"(плеч|голен|бедр|шею\b|шеи\b|предплечь|конечност|затылк).{0,20}защит|"
        r"панцирь.{0,20}сс[оo]|сс[оo].{0,20}панцирь|"
        r"тактическ.{0,15}воротник|воротник.{0,15}тактическ",
        re.I,
    )),
    ("grenade_component", re.compile(
        r"вог[-\s]?1?[0-9]|хвостовик.*(вог|гранат)|стабилизатор.*(мин[ыа]|гранат)|"
        r"взрывател|детонат",
        re.I,
    )),
    ("drone_munitions", re.compile(
        # "груз" bare also false-positives on "разгрузочный"/"разгрузка"
        # (tactical load-bearing vest terminology, shares the root but means
        # something unrelated) — found via "Быстросъемный замок для
        # разгрузки… Жилеты разгрузочные" matching as a drone-drop mechanism.
        # "мин" bare would similarly false-positive on "минимальный"/"минута"
        # etc., so scoped to an actual mine noun form.
        r"сброс.*(fpv|дрон|(?<!раз)(?<!вы)груз|мин[аы]\b|минирован|квадрокоптер)|"
        r"бомбосброс|авиабомб|скидывател|механизм сброса|каспа.*мин[аы]\b|"
        # "сбрасывание" is a distinct stem from "сброс" — see categorize.py's
        # drone_drop_system for the corroborating detail (image-OCR find)
        r"сбрасыва.{0,10}воздух|воздух.{0,10}сбрасыва",
        re.I,
    )),
    ("fpv_drone_combat", re.compile(
        r"(fpv.*(дрон|рама|каркас|квадрокоптер))|(квадрокоптер.*fpv)|"
        # (?<!анти) excludes "антиударный дрон" (shock-resistant toy ball
        # drone) — "ударный дрон" is a literal substring of "антиударный
        # дрон" with no word boundary between "анти" and "ударный" (both
        # are word characters, fused into one compound word), so a plain
        # substring match wrongly tagged 2 children's toy balls as
        # strict_military/fpv_drone_combat
        r"дрон[-\s]?камикадзе|(?<!анти)ударный дрон|"
        r"(fpv.*тепловизор)|(квадрокоптер.*тепловизор)",
        re.I,
    )),
    # A 15-20km fiber-optic spool has no plausible non-drone use at this length —
    # RF-jam-resistant fiber-tethered FPV is current-generation combat drone tech.
    # "оптоволокон" (adjective stem) and "оптоволокно" (bare noun) are NOT
    # substrings of each other despite looking related — see categorize.py's
    # parallel fix for the same bug.
    ("fiber_optic_fpv", re.compile(
        r"оптоволок[а-я]*.*катушк|катушк.*оптоволок[а-я]*|"
        # Bare "оптоволокно" collides with fiber-optic gun sights on
        # spearfishing reels ("Мотовило для буйрепа ... для Glock") — an
        # unrelated domain — so require БПЛА/drone co-occurrence, or the
        # NCZOBOE brand (fiber-optic-drone-link maker, checked full corpus).
        r"оптоволок[а-я]*.{0,20}(бпла|дрон)|(бпла|дрон).{0,20}оптоволок[а-я]*|"
        r"nczoboe",
        re.I
    )),
    # П-274М2 is Soviet/Russian military twin-core field-telephone wire (no
    # civilian telecom use); ТА-57/ТАИ-43 are specific field-telephone models.
    # Found via "Адаптер для телефона полевой связи ТА57" and "Провод П 274
    # М2, для полевой связи" — neither matched any existing comms rule.
    ("field_telephone_wire", re.compile(
        r"п[\s-]?274[\s-]?м\d?|та-?57\b|таи-?43\b|полевой связи|"
        r"полевого телефона",
        re.I,
    )),
    ("weapon_optics", re.compile(
        r"прицел.*(ак-?[0-9]|оружейн|ствол|калашников|снайперск|"
        r"ргп-?7|рпг-?7)|пго-?7[а-я]*\b|"
        r"тепловизионный прицел|коллиматор.*оруж", re.I,
    )),
    # RPG-7 grenadier's carry gear ("рюкзак гранатомётчика") — no plausible
    # civilian use, distinct from generic tactical backpacks.
    ("grenadier_gear", re.compile(
        r"гранатом[её]т.*(рюкзак|сумк|подсумок)|"
        r"(рюкзак|сумк|подсумок).*гранатом[её]т|гранатом[её]тчик",
        re.I,
    )),
    # Bare "подсумок для магазина/гранаты" (AK/RPK/SVD/PM/PP magazine or
    # grenade carrier pouch), no тактическ/штурмов/военн/армейск qualifier
    # — the existing military_uniform rule above already catches the
    # qualified form, but a pouch explicitly sized for a specific military
    # rifle/pistol magazine or a hand grenade has no civilian counterpart
    # regardless of whether the seller also used a "tactical" adjective.
    # Checked full corpus (187 listings): every one names a military
    # weapon/caliber (АК, РПК, СВД, ПМ, ПП) or a grenade model (Ф-1, РГД,
    # ВОГ), zero collision with civilian hunting-cartridge belt pouches
    # (those are tagged separately via calibre gauge, not "магазин"/"АК").
    ("magazine_pouch", re.compile(
        r"подсумок.{0,25}(магазин|гранат)|(магазин|гранат).{0,25}подсумок|"
        r"подгранатник",
        re.I,
    )),
    ("weapon_attachment", re.compile(
        r"глушитель|подствольн|цевье.*(ак|автомат)|дульный тормоз|"
        r"крепление.*(гранатомет|подствольн)|"
        # muzzle-mounted net launcher ("сеткомет", "дронобой") for AK-pattern
        # rifles — found via "Сеткомет антидроновая насадка на ствол
        # (Дронобой) АК-12" falling through with no rule match at all.
        r"сеткомет|дронобой|насадка.*ствол.*ак[-\s]?\d|"
        r"ак[-\s]?\d.*насадка.*ствол",
        re.I,
    )),
    ("ew_counter_drone", re.compile(
        r"комплекс рэб|подавител.*(дрон|бпла|сигнал|частот)|"
        r"антидрон.*(пушк|ружь|комплекс)|"
        r"(рюкзачн|\d+[\s-]?канал).*рэб|рэб.*(рюкзачн|\d+[\s-]?канал)|"
        # "система подавления" (suppression system, noun) vs "подавитель"
        # (suppressor, agent-noun) — different stem, missed by the pattern
        # above. Found via "РЭБ Барьер-6 система подавления БПЛА".
        r"подавлени.*(дрон|бпла|сигнал|частот)|(дрон|бпла).*подавлени|"
        r"рэб.*барьер|барьер.*рэб",
        re.I,
    )),
    # Passive RF-scanner devices that detect a drone's video/control link
    # without jamming it — a sibling of ew_counter_drone, same no-civilian-use
    # reasoning. Covers the "Булат", "Skydroid Ястреб/S-10/S-12" and "МАРС"
    # product families found via keyword search, none of which matched any
    # existing rule and fell through to "other".
    ("drone_detector", re.compile(
        r"детектор.*(дрон|бпла)|(дрон|бпла).*детектор|"
        r"обнаружитель.*(дрон|бпла)|"
        # "VIGI" (TP-Link surveillance brand) — found via a ПВО (air-defense)
        # unit's Telegram wishlist explicitly building an improvised camera/
        # motor-turret drone-surveillance rig around IP cameras of this
        # brand; narrow, brand-specific, checked full corpus (3 matches,
        # zero collision — unlike generic "IP камера"/"коммутатор" which
        # would sweep in unrelated consumer/office IT equipment
        r"\bvigi\b|asel labs|асель лабс",
        re.I,
    )),
    # Accessories for the "Булат"/"МАРС" detector line (antennas, dock
    # stations, batteries, car mounts) — the listing names reuse the model
    # name without the word "детектор", so the rule above misses them.
    # "булат" (also Damascus-steel knives) and "марс" (planet/candy brand)
    # are too generic to match bare; scoped to a version marker, "3mx", or
    # co-occurrence with an accessory/device word.
    ("drone_detector_accessory", re.compile(
        r"3mx|булат.{0,15}\bv?\.?\s?[34]\b|\bv?\.?\s?[34]\b.{0,15}булат|"
        r"марс.{0,20}(антенн|док-станц|аккумулятор|таир|держатель|зарядн|"
        r"бпла|детектор|булат)|"
        r"(антенн|док-станц|аккумулятор|таир|держатель|зарядн|бпла|детектор|"
        r"булат).{0,20}марс",
        re.I,
    )),
    ("reload_components", re.compile(
        r"\bкапсюл|порох\b|гильз[аы] для патрон", re.I,
    )),
]

_DUAL_USE_RULES: list[tuple[str, re.Pattern]] = [
    ("nutrition", re.compile(
        r"сублимат|сухпаек|сухой\s?паек|тушенк|паштет консерв|провизия|"
        r"рацион питания|\bпаек\b|энергетик|витамин|\bбад[ыа]?\b", re.I,
    )),
    ("hygiene", re.compile(
        # "Helmetex Army" gear/uniform odor neutralizer — genuinely
        # brand-scoped and marketed explicitly for military equipment care
        # (бронежилет, берцы, каска военная), unlike the mass-market
        # commodities below — wildberries.ru/catalog/8193649
        r"helmetex",
        re.I,
    )),
    # bare hygiene match (shampoo/deodorant/razors/soap/toilet paper/
    # towels/laundry powder) was removed entirely — the original corroboration
    # was a front-line unit's BULK crowdfunding wishlist (many units requested
    # together), but every one of these is a fungible mass-market commodity
    # sold to any household; an individual retail SKU gives no way to tell a
    # unit's bulk purchase from ordinary shopping. Found via tracing a
    # "similar"-discovery crawl seeded from a sleeping bag straight into
    # generic men's anti-dandruff shampoo (including global retail brands
    # like Old Spice) and Lenor laundry detergent — 38+19 zero-signal SKUs
    # caught this way alone. Still kept in categorize.py's hygiene_supplies
    # functional category (no war-relevance claim there). Re-add here only
    # per-item via Telegram item_mentions corroboration, not blanket regex.
    # Night vision has civilian hunting/wildlife-watching use, but demand here is
    # overwhelmingly combat-driven — dual-use, not strict, same reasoning as
    # thermal_spotting below.
    ("night_vision", re.compile(
        r"прибор ночного видения|ночного видения|\bпнв\b|night\s?vision", re.I,
    )),
    # Checked before "power" below: a rugged phone that happens to also be a
    # power bank ("Защищенный телефон ... Power Bank для службы в армию")
    # would otherwise match the bare "power bank" branch first and get
    # mislabeled — it's a comms device, not power equipment.
    ("military_phone", re.compile(
        r"телефон.*(службы? в армию|для армии|военнослужащ)|"
        r"(службы? в армию|для армии|военнослужащ).*телефон",
        re.I,
    )),
    ("power", re.compile(
        r"power[\s\-]?bank|повербанк|генератор|солнечн.*панел|солнечн.*зарядк|"
        r"портативн.*электростанц|внешний аккумулятор|резервный источник питания|"
        # extension cords/power strips — confirmed via a front-line
        # drop-ship channel's shopping list, generator-adjacent field
        # electrical infrastructure
        r"удлинитель.{0,10}(сетев|заземл)|сетевой фильтр|"
        # diesel/portable heaters explicitly marketed for dugout use, not
        # just generic camping — wildberries.ru/catalog/598437797
        r"отопител.{0,40}блиндаж|блиндаж.{0,40}отопител|"
        r"обогреватель.{0,40}блиндаж|блиндаж.{0,40}обогреватель",
        re.I,
    )),
    # Excludes "защита/накидка/плащ от тепловизора" — anti-drone cloth mentions
    # "тепловизор" as the thing it defeats, not an actual thermal-imaging
    # device. Stem "тепловиз" (not just "тепловизор") so adjective forms
    # match too — "тепловизионная камера" (a thermal sensor module, e.g. the
    # Foxeer FT640L sold for FPV strike drones) shares no substring with the
    # noun "тепловизор" otherwise and was falling through to "other".
    ("thermal_spotting", re.compile(
        r"(?<!защита от )(?<!накидка от )(?<!плащ от )"
        r"тепловиз|монокуляр тепловизионн|"
        # "arkon"/"arma" bare — same fix as categorize.py's thermal_optics,
        # mount/adapter/cap accessories for the Arkon thermal-scope line
        # that don't repeat "тепловизор" themselves
        r"[aа]rk[oо]n|\barma\b",
        re.I,
    )),
    ("cold_weather", re.compile(
        r"спальн(ый|ик)|термобель|носки шерстян|окопная свеча|топливные таблетк|"
        r"грелка хим|"
        # same field-survival extension as categorize.py's field_comfort
        r"репеллент|антиклещ|обеззаражива.{0,15}вод|хлорка в таблетк|"
        r"самонагрева.{0,5}грелк|грелк.{0,5}самонагрева|"
        # folding field cot — same drop-ship channel corroboration
        r"раскладушк", re.I,
    )),
    ("medical", re.compile(
        r"турникет|жгут|гемостатик|аптечк[аи] (войсков|тактическ|армейск)|"
        r"перевязочный пакет|\bипп\b|\bппи\b|"
        # same combat-trauma extension as categorize.py's medical category —
        # confirmed via Telegram front-line medical wishlist posts
        r"марл[яе]|носилк|космопор|cosmopor|окклюзионн.{0,3}пластыр|противоожогов|"
        r"щипц[а-я]*.{0,10}кусачк|кусачк[а-я]*.{0,10}костн|"
        r"транексам|торакальн.{0,5}катетер|катетер.{0,5}троакар|"
        r"ворот[а-я]* шанца|игл[а-я]* спинальн|спинальн.{0,5}игл|"
        r"тампонад|воздуховод|назофарингеальн|зажим москит|"
        r"катетер внутривен|капельниц|костыл|натрия хлорид|"
        r"бланк.{0,15}первой помощи|патологоанатомическ|"
        r"ножницы.{0,10}медицинск|воскопран|"
        # Adult diapers/incontinence pads for wounded hospital patients —
        # confirmed via t.me/NASHIM37/26150 (unit thank-you post for a
        # donation delivered to a hospital ward). Distinct from the much
        # larger baby-diaper noise cluster (kg-weight sizing, "новорожденных",
        # Ultra Comfort/Elite Soft brands), left untouched — this is scoped
        # to "для взрослых" wording specifically.
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
        # spec table lists "сухой душ армейский" — genuinely dual-use, unlike
        # the mass-market shampoo/deodorant this session already walked back
        r"сухой душ|для лежач.{0,15}(полев|сво)|"
        # bare tactical trauma-shears/ampoule-case, found sitting in "other"
        # despite real Telegram-mention corroboration (11-19 mentions) —
        # checked full corpus, clean
        r"ножниц.{0,15}тактическ|тактическ.{0,15}ножниц|ампульниц|"
        r"очки.{0,10}маск.{0,10}тактическ|маск.{0,10}очки.{0,10}тактическ|"
        # bare шприц/бинт/аптечк/кровоостанав/перевязочн/гемостатич — this
        # axis required narrower co-occurrence (e.g. "аптечк[аи] военн")
        # than categorize.py's medical category, which already bare-matches
        # these; checked full corpus, zero non-medical-category collision.
        # "гемостатик" broadened to "гемостатич" — "гемостатический" is a
        # separate derivational stem, same class of bug as
        # баллистик/баллистический found earlier
        r"шприц|\bбинт|аптечк|кровоостанав|перевязочн|гемостатич",
        re.I,
    )),
    ("lighting_tools", re.compile(
        r"фонарь налобн|мультитул|паракорд|скотч армированн|стяжк[аи] пластиков",
        re.I,
    )),
    # Military-spec boots ("уставные", "тактические") — civilian hiking-boot
    # crossover exists but demand here is combat-driven, same reasoning as
    # night_vision/thermal_spotting above.
    ("boots", re.compile(
        r"берц|"
        # "Ботинки/кроссовки тактические" without the word "берцы" —
        # same corroboration as military_uniform above
        r"(ботинк|кроссовк).*тактическ|тактическ.*(ботинк|кроссовк)",
        re.I,
    )),
    # Shelter-half/rain-cape combo, classic Soviet/Russian army field gear —
    # some camping/tourism crossover, same reasoning as boots above.
    ("shelter_cape", re.compile(r"плащ.?палатк", re.I)),
    # "рация" alone misses declined forms (рации, раций) and Starlink/radio
    # accessories (antennas, cases, cables, mounts) which don't repeat the
    # word but are clearly the same demand category as the base equipment.
    # Negative lookbehind guards against a collision found via a raw
    # search-goods.wildberries.ru probe: "раци[ияюе]"/"раций" is a substring
    # of ANY word ending "-рация"/"-рации" (фильтрация, операция, декорация,
    # регенерация, вибрация) — a kitchen-strainer listing wrongly matched.
    ("comms", re.compile(
        r"(?<![а-яё])раци[ияюе]|радиостанц|баофенг|baofeng|walkie|starlink|старлинк|"
        r"спутниковой связ|спутников.{0,10}интернет|"
        # cellular signal booster explicitly marketed for dugout/bunker use,
        # not just home/apartment — wildberries.ru/catalog/866024695
        r"усилител.{0,15}сигнал.{0,40}(блиндаж|землянк)|"
        r"(блиндаж|землянк).{0,40}усилител.{0,15}сигнал|"
        # bare brand/protocol tokens sold without the Russian word "рация"
        # at all, e.g. "LIRA DP-2600V DMR VHF 136-174 МГц шифрование" —
        # nm 974126730/989741418 sat in other/other despite being encrypted
        # DMR handhelds, because every other trigger in this rule expects
        # "рация"/"радиостанция" to appear somewhere in the title.
        r"\btyt\b|\bdmr\b|тангента.*(?<![а-яё])раци[ияюе]|"
        r"антенн.*(?<![а-яё])раци[ияюе]|(?<![а-яё])раци[ияюе].*антенн",
        re.I,
    )),
    # FPV video-link components (VTX/receiver/goggles/antenna) sold bare,
    # without "рама"/"каркас"/"дрон"/"квадрокоптер" co-occurring — those get
    # fpv_drone_combat above. Racing/freestyle FPV is a genuine civilian
    # hobby, but demand for these parts here skews combat, same reasoning as
    # night_vision/boots above.
    # Same components also get sold bare under an FPV-exclusive brand name
    # or "для дронов" instead of the literal word "fpv" (e.g. "Антенна GEPRC
    # SOMA 1.3G для дронов", "Аккумулятор LiPo Vant 22000мАч 100C 8S") —
    # brand tokens are unambiguous since these makers build FPV parts only.
    ("fpv_component", re.compile(
        r"(fpv|фпв).*(передатчик|vtx|приемник|антенн|гоггл|очки видео|"
        r"плата управлени|менеджер питани|полетный контроллер|esc\b|pdb\b|"
        r"зарядн)|"
        r"(передатчик|vtx|приемник|антенн|гоггл|очки видео|плата управлени|"
        r"менеджер питани|полетный контроллер|esc\b|pdb\b|зарядн).*(fpv|фпв)|"
        r"(антенн|аккумулятор|батаре|усилител).*(дрон|квадрокоптер)|"
        r"(дрон|квадрокоптер).*(антенн|аккумулятор|батаре|усилител)|"
        r"geprc|peakfpv|betafpv|caddx|hqprop|gemfan|walksnail|rushfpv|axisflying|"
        r"alientech|"
        r"\bvant\b.*\d+c\b|\d+c\b.*\bvant\b|\bxt60\b|"
        # bare "lipo"/"липо" and FPV patch-antenna brand names — same fix
        # as categorize.py's fpv_drone, mirrored here (checked full corpus,
        # zero collision with non-FPV batteries/antennas)
        r"\blipo\b|липо|\brhcp\b|\blhcp\b|pagoda|lumenier|\baxii\b|"
        r"патч.*антенн|антенн.*патч|"
        # DJI/Mavic bare — same fix as categorize.py's fpv_drone
        r"\bdji\b|\bmavic\b|\bмавик\b|"
        # RF amplifiers scoped to the 2.4/5.8 GHz FPV control+video bands —
        # band qualifier separates these from car-audio amps (see the
        # "alientech duo усилитель" keyword-noise note in config/keywords.txt)
        r"усилител.{0,30}[25][ .,]?[48]\s?ггц|[25][ .,]?[48]\s?ггц.{0,30}усилител|"
        r"наземн.{0,3}станц.{0,3}управлен|\bнсу\b|"
        # bare "fpv"/"дрон"/"квадрокоптер"/"коптер" — this axis had a much
        # narrower accessory-word-cooccurrence requirement than
        # categorize.py's bare fpv_drone category match, so motors, frames,
        # flight controllers, VTX modules etc. (the bulk of the FPV parts
        # ecosystem this whole site is about) fell through to "other" unless
        # a specific accessory word happened to co-occur. Same dual-use
        # reasoning as the individual brand names already listed above
        # (consumer/hobby market + real front-line demand riding along).
        # Toy/kids drone collision (a "flying ball" novelty item) handled by
        # the _REPLICA gate above, not here.
        r"fpv|фпв|квадрокоптер|\bкоптер[а-я]*\b|\bдрон[а-я]*\b",
        re.I,
    )),
    ("anti_drone_cloth", re.compile(
        r"(антидрон.*(плащ|накидк|понч|сеть|покрывал))|"
        r"((плащ|накидк|понч|сеть|покрывал).*антидрон)|"
        r"защит[ао].*тепловизор.*(плащ|накидк|понч|одеял)|"
        r"(плащ|накидк|понч|одеял).*защит[ао].*тепловизор",
        re.I,
    )),
    ("camo_shelter", re.compile(
        r"маскировочн.*сет|тент брезент|тарпаулин|"
        r"камуфляжн.{0,10}сеть|сеть.{0,10}камуфляжн|"
        r"маскирующ.{0,10}сет|сет.{0,10}маскирующ|"
        r"затеняющ.{0,10}сеть|сеть.{0,10}затеняющ|"
        # dugout ("блиндаж") insulation — same corroboration as
        # categorize.py's construction_tools extension
        r"фольгированный утеплитель|утеплитель.{0,10}(стен|пол)|"
        # dugout ("блиндаж") sealant — confirmed via
        # t.me/ghost_of_novorossia/27206 ("Монтажная пена для блиндажей")
        r"монтажн.{0,5}пен|пен.{0,5}монтажн",
        re.I)),
    ("drone_footage", re.compile(r"sd[\s\-]?карт|карта памяти", re.I)),
    ("field_construction", re.compile(
        r"бензопил|сапёрная лопат|шанцев|"
        # chainsaw bar/chain oil — the consumable side of the same
        # combat-engineer demand (t.me/ghost_of_novorossia/27206)
        r"масло.{0,10}(цепн|пильн)|(цепн|пильн).{0,10}масло",
        re.I)),
    # Sapper's grapple hook — mine-clearance/route-checking use, not just
    # camping, regardless of whether the listing also says "тактическ".
    ("sapper_gear", re.compile(
        r"крюк.?кошка|саперн.*кошк|кошк.*саперн|подсумок.*саперн|"
        r"крюк.*саперн|саперн.*крюк|стропа эвакуационн|"
        # entrenching shovels — confirmed via a front-line drop-ship
        # channel's live shopping list explicitly needing "Лопаты"
        r"лопата (штыков|совков)|лопат[аы] сапер", re.I)),
]


def classify_military(name: str | None, subj_name: str | None) -> dict[str, str | None]:
    text = normalize(f"{name or ''} {subj_name or ''}")
    # Name only, deliberately excluding subj_name — subj_name is WB's own
    # catalog placement (e.g. "Сумки спортивные", "Перчатки спортивные"),
    # not a claim about the product. Checking the combined text here caught
    # 55 items via subj_name alone (a tactical belt-pouch catalogued under
    # "Сумки спортивные" isn't civilian gear just because WB's own taxonomy
    # filed it oddly — that WB-taxonomy-is-absurd pattern is itself covered
    # by docs/index.html's #taxonomy section and shouldn't double back into
    # classifying the item as civilian-market on that basis).
    name_only = normalize(name or "")
    if _MERCH.search(text) or _REPLICA.search(text):
        return {"military_class": "other", "military_reason": None}
    if _INTERIOR_MINISTRY.search(text) and not _GENUINE_MILITARY_MARK.search(text):
        return {"military_class": "other", "military_reason": None}
    if _DRONE_GIMMICK.search(text):
        return {"military_class": "other", "military_reason": None}
    if _SAPPER_HOOK.search(text):
        return {"military_class": "dual_use_demand", "military_reason": "sapper_gear"}
    for label, pattern in _STRICT_RULES:
        if pattern.search(text):
            if (
                label in _CIVILIAN_SPORT_HUNT_SCOPED_REASONS
                and _CIVILIAN_SPORT_HUNT.search(name_only)
                and not _GENUINE_MILITARY_MARK.search(text)
            ):
                return {"military_class": "dual_use_demand", "military_reason": label}
            return {"military_class": "strict_military", "military_reason": label}
    for label, pattern in _DUAL_USE_RULES:
        if pattern.search(text):
            return {"military_class": "dual_use_demand", "military_reason": label}
    return {"military_class": "other", "military_reason": None}
