"""One-off content generator for the public site's visual sections.

Not part of the crawl pipeline: reads the already-populated DB and fetches a
small, curated set of already-known gallery-image URLs (bounded, not a sweep)
via the existing wb/http helpers. Run by hand, review the printed markup, then
splice it into docs/index.html — this is a content-authoring aid, not a
template engine or build step that runs automatically.

Usage:
    python -m wb_watch.site.build_assets
"""
from __future__ import annotations

import html
import io

from PIL import Image

from .. import config, db, http

DOCS_IMAGES_DIR = config.ROOT / "docs" / "images"

# Curated gallery pairs: (nm_id, confirm_image_index, caption). One representative
# per distinct finding rather than all 5 near-duplicate ИК-Ремиссия colorways —
# avoids the gallery repeating the same marketing image five times. See
# conversation notes / RESEARCH_BRIEF.md for how each was found.
#
# Deliberately excludes pure civilian merch (flags, mugs, books, patches) even
# when field_use_signal fires on them — a "СВО" novel or a flag doesn't
# demonstrate a military supply channel the way equipment does; it dilutes
# the section's evidentiary weight. Dropped nm 239118353 (a book) for this
# reason. Also excludes nutrition_rations hits on "Штурм"/"Боевой комплект"
# branded supplements (nm 896069564, 434895638) — a known, deliberately
# unfixed false-positive class (men's-fitness marketing jargon, not SVO
# content; see signals.py calibration notes) that would be actively wrong to
# feature as evidence here.
GALLERY_ITEMS: list[tuple[int, int, str]] = [
    (604244329, 2, '«Выбор наших героев» — кабель на карточке с фотографией военного в боевой экипировке.'),
    (482699353, 3, 'ИК-Ремиссия: ткань подсумка, снижающая заметность бойца для приборов ночного видения — прямо в описании товара (один из 5 найденных цветов той же линейки).'),
    (591122232, 1, '«Бойцы СВО рекомендуют» — прямо на инфографике тактического жилета.'),
    (228496327, 1, 'Тот же слоган — «Бойцы СВО рекомендуют» — на карточке другого продавца: это не разовая надпись, а повторяющийся маркетинговый приём.'),
    (62010384, 6, 'Таблетки для обеззараживания воды: на упаковке прямо напечатано «...помощь на СВО... для дачи. ГОТОВ».'),
    (143128680, 1, 'Складной сапёрный крюк-«кошка», продающийся как турпоходный инвентарь для сбора хвороста, — с инфографикой «для разминирования, для эвакуации».'),
    (133601677, 1, 'Ранозаживляющее средство в порошке — на карточке прямо изображены «боевых ран», «осколочные ранения».'),
    (149128985, 5, 'Инструкция по применению аптечки: «применим при ранениях грудной клетки».'),
    (215070113, 1, 'Кровоостанавливающий жгут, брендированный под МЧС, — с припиской «зарекомендовал себя на СВО».'),
    (210767188, 1, 'Чехол для бронежилета «Невский 2.0»: та же ИК-Ремиссия и «стропа эвакуационная» на одной инфографике.'),
    (318340889, 1, 'Военный тактический шлем: инфографика прямо гласит «ПРОВЕРЕН НА СВО».'),
    (271682215, 3, 'Антидроновый плащ-накидка от тепловизора: «Многократно проверено на ЛБС» — линия боевого соприкосновения, военная аббревиатура прямо в карточке.'),
    (276341835, 4, 'Тот же тип бронежилета с тканью «ИК-Ремиссия»: на карточке прямо сказано, что ткань «рассеивает инфракрасное излучение, делая бойца невидимым для приборов ночного видения».'),
    (918230226, 2, 'Механизм сброса грузов «Колосник» для гражданского дрона DJI Mavic 3: на одном из фото в галерее — уже закреплённый в держателе боеприпас с маркировкой «ОФСП-0,5», «КЗСП-0,5», «ОФСП-0,8».'),
    (287345219, 5, 'Кровоостанавливающий жгут: «применяется военными в реальных боевых условиях», «подходит для аптечек 1-го и 2-го эшелона» — военно-медицинский термин для эвакуации раненых.'),
    (422685389, 1, 'Портативный детектор дронов, спасающий «жизни наших бойцов», прямо на инфографике антидронового детектора.'),
    (819725159, 5, 'Маскировочная сеть: инфографика прямо перечисляет «боевые сценарии» применения, а не только охоту/рыбалку.'),
    (557275222, 4, 'Антидроновое одеяло-накидка: «для блиндажей и огневых позиций, для укрытия техники и личного состава» — фортификационная, а не туристическая лексика.'),
    (482855859, 1, 'Спальный мешок: инфографика прямо гласит «проверен на СВО», рядом с описанием влагостойкости и слойности утеплителя.'),
    (346165329, 1, 'Антидроновое пончо от тепловизора: «проверен штурмовиками!» прямо на инфографике.'),
    (918230228, 3, 'Система сброса грузов «МРАК» для DJI Mavic 3: на одном из фото в галерее — уже закреплённые на дроне боеприпасы (инертный/учебный и штатный), подпись «сход боеприпасов строго вертикально».'),
    (918264582, 2, 'Ещё один механизм сброса грузов, «Моржоми4Т», — для тепловизионного дрона DJI Matrice 4T: та же формулировка, что у «Колосника», — «работает без GPS», «перпендикулярное расположение боеприпаса».'),
    (846399511, 2, 'Система быстрого сброса бронежилета: ситуации применения указаны прямым текстом — «при падении в воду» и «при ранении — когда дыхание затруднено».'),
    (327612688, 1, 'Жгут кровоостанавливающий «Серёгин»: на карточке заявлено — «наши фронтовые медики рекомендуют».'),
    (949232819, 1, 'Турникет так и называется — «Штурмовой тактический турникет ZМЕЙ».'),
    (248904672, 4, 'Жгут кровоостанавливающий: на карточке прямо указано — «применяется военными в реальных боевых условиях».'),
    (543831218, 3, 'Укомплектованный подсумок-аптечка «1-го эшелона» от бренда «Боевой стиль»: турникет, тактические ножницы и маркер для отметки времени наложения жгута.'),
    # nm 239623431: image 2 is a pricing/quality warning slide, not a usable
    # product shot — docs/images/239623431_product.jpg was hand-picked from
    # image 4 instead; rerunning build_gallery_images() would overwrite it
    # with image 2 unless this function is taught to special-case it.
    (239623431, 1, 'Тактический шлем: «проверено на СВО», в комплекте — сертификат РФ и видео отстрела шлема.'),
    (321011002, 1, 'Ещё один военный шлем с той же формулировкой — «проверен на СВО», с символом «Z» и иллюстрацией бойца с оружием.'),
    (698859142, 1, 'Жгут кровоостанавливающий «Ленинградец 5 Штурмовик»: на карточке заявлено — «разработано совместно с действующими специалистами Министерства обороны РФ».'),
    (450816289, 5, 'Спальный мешок: собственная инфографика продавца делит аудиторию на четыре панели — «СВО», рыбаки, охотники, туристы — с фотографией бойца на позиции.'),
    (898708889, 3, '«Армейский спальный мешок ВКПО» — само название карточки не оставляет пространства для «туристической» интерпретации.'),
    (488928364, 6, '«Рюкзак гранатомётчика на 6 выстрелов РПГ-7»: гарантия производителя прямо разделяет «окопные условия» и «гражданские условия» эксплуатации.'),
    (797500540, 3, 'Антидроновый плащ «МОРОК»: на карточке — переписка с реальными тестировщиками («ребята из Курска протестили накидки»), а не рекламный текст.'),
    (326298293, 5, 'Антидроновая маскировочная сеть: реальное фото сетки на входе в блиндаж в заснеженном лесу, с подписью «для блиндажей, для огневых позиций, для укрытия техники и личного состава».'),
]


def _webp_to_jpg(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()


def build_gallery_images(conn) -> list[dict]:
    DOCS_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for nm_id, confirm_idx, caption in GALLERY_ITEMS:
        item = conn.execute(
            "SELECT name FROM items WHERE nm_id=?", (nm_id,)
        ).fetchone()
        if not item:
            print(f"[skip] nm {nm_id} not found in items table")
            continue
        rows = {
            r["image_index"]: r["url"]
            for r in conn.execute(
                "SELECT image_index, url FROM item_images WHERE nm_id=?", (nm_id,)
            )
        }
        product_idx = 1 if 1 in rows and 1 != confirm_idx else next(
            (i for i in sorted(rows) if i != confirm_idx), None
        )
        if product_idx is None or confirm_idx not in rows:
            print(f"[skip] nm {nm_id} missing product or confirm image row")
            continue

        product_path = DOCS_IMAGES_DIR / f"{nm_id}_product.jpg"
        confirm_path = DOCS_IMAGES_DIR / f"{nm_id}_confirm.jpg"
        for idx, path in ((product_idx, product_path), (confirm_idx, confirm_path)):
            data = http.get_bytes(rows[idx])
            if not data:
                print(f"[fail] nm {nm_id} image {idx}: fetch failed")
                break
            path.write_bytes(_webp_to_jpg(data))
        else:
            results.append({
                "nm_id": nm_id,
                "name": item["name"],
                "caption": caption,
                "product_file": product_path.name,
                "confirm_file": confirm_path.name,
            })
            print(f"[ok] nm {nm_id}: {product_path.name}, {confirm_path.name}")
    return results


def render_gallery_html(items: list[dict]) -> str:
    def pair(it):
        url = f"https://www.wildberries.ru/catalog/{it['nm_id']}/detail.aspx"
        name = html.escape(it["name"])
        caption = html.escape(it["caption"])
        return f"""      <a class="gallery-pair" href="{url}" target="_blank" rel="noopener">
        <div class="gallery-imgs">
          <img src="images/{it['product_file']}" alt="{name}" loading="lazy">
          <img src="images/{it['confirm_file']}" alt="Подтверждающее изображение" loading="lazy">
        </div>
        <div class="gallery-cap">{caption}</div>
      </a>"""

    track = "\n".join(pair(it) for it in items)
    # Duplicated once for a seamless CSS-only loop.
    return f"""  <section id="visual-gallery">
    <h2>Что подтверждают сами изображения</h2>
    <p>Не только текст названия и отзывы — сами карточки на Wildberries нередко публикуют изображения, прямо подтверждающие военное назначение товара. Ниже — то, что мы нашли, сканируя фотогалереи карточек (полный список: см. «Данные»):</p>
    <div class="gallery-viewport">
      <div class="gallery-track">
{track}
{track}
      </div>
    </div>
  </section>"""


def build_review_ticker(conn, limit: int = 36) -> list[dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT r.nm_id, i.name AS item_name, r.field_use_phrase,
               r.created_date
        FROM reviews r
        LEFT JOIN items i ON i.imt_id = r.imt_id
        WHERE r.field_use_signal = 1 AND i.name IS NOT NULL
        ORDER BY r.created_date DESC
        """
    ).fetchall()
    seen_nm = set()
    out = []
    for r in rows:
        if r["nm_id"] in seen_nm:
            continue
        seen_nm.add(r["nm_id"])
        out.append({
            "nm_id": r["nm_id"],
            "name": r["item_name"],
            "phrase": (r["field_use_phrase"] or "").strip(),
        })
        if len(out) >= limit:
            break
    return out


def render_ticker_html(reviews: list[dict]) -> str:
    def row(r):
        url = f"https://www.wildberries.ru/catalog/{r['nm_id']}/detail.aspx"
        phrase = html.escape(r["phrase"].replace("\n", " "))
        name = html.escape(r["name"][:70])
        return (
            f'        <a class="ticker-row" href="{url}" target="_blank" rel="noopener">'
            f'<span class="ticker-item">{name}</span>'
            f'<span class="ticker-quote">«…{phrase}…»</span></a>'
        )

    rows = "\n".join(row(r) for r in reviews)
    return f"""  <section id="review-ticker">
    <h2>Что пишут покупатели</h2>
    <p>{len(reviews)} карточек ниже — из {'{n_total}'} отзывов с прямым подтверждением военного назначения (полный список — в data/field_use_reviews.csv):</p>
    <div class="ticker-viewport">
      <div class="ticker-track">
{rows}
{rows}
      </div>
    </div>
  </section>"""


def render_news_ticker_html(conn, limit: int = 8) -> str:
    """Render the "Wildberries/Kim in the press" box from news_items,
    populated by `wb-watch news-scan` (see pipeline/news_scan.py)."""
    import datetime as _dt

    from .. import db as _db

    rows = _db.recent_news_items(conn, limit=limit)
    items = []
    for r in rows:
        date_str = ""
        if r["published"]:
            try:
                dt = _dt.datetime.fromisoformat(r["published"])
                months = ["янв", "февр", "марта", "апр", "мая", "июня", "июля",
                          "авг", "сент", "окт", "нояб", "дек"]
                date_str = f'{dt.day} {months[dt.month - 1]} {dt.year}'
            except ValueError:
                date_str = ""
        items.append(
            f'      <li><span class="news-outlet">{html.escape(r["outlet"])}</span> '
            f'<a href="{html.escape(r["url"])}" target="_blank" rel="noopener">'
            f'{html.escape(r["title"])}</a> '
            f'<span class="news-date">{date_str}</span></li>'
        )
    rows_html = "\n".join(items)
    return f"""  <div class="newsbox ui">
    <div class="newsbox-label">Wildberries и Татьяна Ким в прессе — по времени публикации</div>
    <ul class="newsbox-list">
{rows_html}
    </ul>
  </div>"""


_CATEGORY_LABELS = {
    "other": "не относится ни к одной категории",
    "power_energy": "повербанки, генераторы, зарядные станции",
    "fpv_drone": "FPV-дроны и компоненты",
    "field_comfort": "спальники, термобельё, полевой быт",
    "comms_ew": "рации, РЭБ, полевая связь",
    "body_armor": "бронежилеты и бронеплиты",
    "medical": "аптечки, жгуты, медицина",
    "tactical_wear": "тактическая форма и обмундирование",
    "night_vision": "приборы ночного видения",
    "camo_netting": "маскировочные сети",
    "thermal_optics": "тепловизионная оптика",
    "combat_helmet": "боевые каски и шлемы",
    "sapper_gear": "сапёрное снаряжение",
    "military_merch": "военный мерч (шевроны, сувениры)",
    "anti_drone_gear": "антидроновые системы",
    "construction_tools": "стройматериалы и инструмент",
    "drone_drop_system": "системы сброса для дронов",
    "toy_costume": "детские игровые костюмы",
    "lighting": "фонари",
    "nutrition_rations": "продовольствие и рационы",
    "munitions_component": "компоненты боеприпасов",
    "uncertain": "неясное назначение",
}


def build_category_chart(conn) -> str:
    rows = conn.execute(
        """
        SELECT category,
               SUM(CASE WHEN military_class='strict_military' THEN 1 ELSE 0 END) AS strict,
               SUM(CASE WHEN military_class='dual_use_demand' THEN 1 ELSE 0 END) AS dual,
               SUM(CASE WHEN military_class='other' THEN 1 ELSE 0 END) AS other,
               COUNT(*) AS total
        FROM items
        GROUP BY category
        ORDER BY total DESC
        """
    ).fetchall()
    top = rows[:12]
    omitted = rows[12:]
    if omitted:
        omitted_total = sum(r["total"] for r in omitted)
        print(f"[chart] omitting {len(omitted)} smaller categories ({omitted_total} items): "
              + ", ".join(f"{r['category']}={r['total']}" for r in omitted))

    max_total = max(r["total"] for r in top)
    bar_h = 22
    gap = 10
    label_w = 300
    chart_w = 340
    height = len(top) * (bar_h + gap)

    def seg(x, w, cls):
        return f'<rect x="{x:.1f}" y="0" width="{w:.1f}" height="{bar_h}" class="cat-{cls}"/>'

    rows_svg = []
    for i, r in enumerate(top):
        y = i * (bar_h + gap)
        scale = chart_w / max_total
        x = 0.0
        segs = []
        for cls, val in (("strict", r["strict"]), ("dual", r["dual"]), ("other", r["other"])):
            w = val * scale
            if w > 0:
                segs.append(seg(x, w, cls))
                x += w
        rows_svg.append(
            f'<g transform="translate(0,{y})">'
            f'<text x="{label_w - 10}" y="{bar_h/2+4}" class="cat-label">'
            f'{html.escape(_CATEGORY_LABELS.get(r["category"], r["category"]))}</text>'
            f'<g transform="translate({label_w},0)">{"".join(segs)}</g>'
            f'<text x="{label_w + chart_w + 8}" y="{bar_h/2+4}" class="cat-val">{r["total"]}</text>'
            f'</g>'
        )

    svg = (
        f'<svg viewBox="0 0 {label_w + chart_w + 60} {height}" role="img" '
        f'aria-label="Число карточек по функциональной категории, с разбивкой по военному статусу">'
        + "".join(rows_svg) + "</svg>"
    )
    return svg


def build_timeline_chart(conn) -> str:
    rows = conn.execute(
        """
        SELECT substr(wb_create_date,1,7) AS month, COUNT(*) AS n
        FROM items
        WHERE wb_create_date IS NOT NULL AND wb_create_date != ''
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    covered = sum(r["n"] for r in rows)
    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    print(f"[timeline] wb_create_date coverage: {covered}/{total} ({100*covered/total:.0f}%)")

    max_n = max(r["n"] for r in rows)
    bar_w = 6
    gap = 2
    height = 90
    width = len(rows) * (bar_w + gap)
    bars = []
    for i, r in enumerate(rows):
        h = (r["n"] / max_n) * height
        x = i * (bar_w + gap)
        bars.append(
            f'<rect x="{x}" y="{height-h:.1f}" width="{bar_w}" height="{h:.1f}" class="tl-bar">'
            f'<title>{r["month"]}: {r["n"]}</title></rect>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Число карточек по месяцу создания на Wildberries">'
        + "".join(bars) + "</svg>"
    )


def build_brand_concentration_chart(conn, top_n: int = 12) -> str:
    """A handful of brands behind a large share of strict-military listings —
    reinforces the #sellers section's "structured supply, not one-off
    individuals" point with a visual. "Нет бренда" (no declared brand) is
    excluded: it's an aggregation bucket across ~30 unrelated sellers, not a
    real brand concentration."""
    rows = conn.execute(
        """
        SELECT brand, COUNT(DISTINCT supplier_id) AS n_sellers, COUNT(*) AS n_items,
               SUM(CASE WHEN military_class='strict_military' THEN 1 ELSE 0 END) AS n_strict
        FROM items
        WHERE brand IS NOT NULL AND brand != '' AND brand != 'Нет бренда'
        GROUP BY brand
        ORDER BY n_strict DESC
        LIMIT ?
        """,
        (top_n,),
    ).fetchall()

    max_n = max(r["n_strict"] for r in rows)
    bar_h = 22
    gap = 10
    label_w = 220
    chart_w = 340
    height = len(rows) * (bar_h + gap)
    scale = chart_w / max_n

    bars = []
    for i, r in enumerate(rows):
        y = i * (bar_h + gap)
        w = r["n_strict"] * scale
        sellers_note = f" ({r['n_sellers']} прод.)" if r["n_sellers"] > 1 else " (1 прод.)"
        label = html.escape(r["brand"]) + sellers_note
        bars.append(
            f'<g transform="translate(0,{y})">'
            f'<text x="{label_w - 10}" y="{bar_h/2+4}" class="cat-label">{label}</text>'
            f'<rect x="{label_w}" y="0" width="{w:.1f}" height="{bar_h}" class="cat-strict"/>'
            f'<text x="{label_w + chart_w + 8}" y="{bar_h/2+4}" class="cat-val">{r["n_strict"]}</text>'
            f'</g>'
        )
    return (
        f'<svg class="cat-chart" viewBox="0 0 {label_w + chart_w + 60} {height}" role="img" '
        f'aria-label="Число карточек безусловно военного назначения по бренду, топ-{top_n}">'
        + "".join(bars) + "</svg>"
    )


def build_stat_cards(conn) -> dict:
    total_items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    scanned = conn.execute("SELECT COUNT(DISTINCT nm_id) FROM item_images").fetchone()[0]
    confirmed = conn.execute(
        "SELECT COUNT(*) FROM item_images WHERE field_use_signal=1"
    ).fetchone()[0]
    return {"total_items": total_items, "scanned": scanned, "confirmed": confirmed}


# NOTE: the news box is now DB-backed — see render_news_ticker_html() above,
# fed by `wb-watch news-scan` (pipeline/news_scan.py) against Meduza/
# Mediazona/Kommersant/RBC RSS. Re-run news-scan periodically to refresh it.


def main():
    conn = db.connect()

    print("=== news box ===")
    print(render_news_ticker_html(conn))
    print()

    print("=== gallery ===")
    gallery_items = build_gallery_images(conn)
    print()
    print(render_gallery_html(gallery_items))
    print()

    print("=== review ticker ===")
    reviews = build_review_ticker(conn)
    total_confirmed = conn.execute(
        "SELECT COUNT(*) FROM reviews WHERE field_use_signal=1"
    ).fetchone()[0]
    ticker_html = render_ticker_html(reviews).replace("{n_total}", str(total_confirmed))
    print(ticker_html)
    print()

    print("=== category chart (svg) ===")
    print(build_category_chart(conn))
    print()

    print("=== timeline chart (svg) ===")
    print(build_timeline_chart(conn))
    print()

    print("=== brand concentration chart (svg) ===")
    print(build_brand_concentration_chart(conn))
    print()

    print("=== stat cards ===")
    print(build_stat_cards(conn))


if __name__ == "__main__":
    main()
