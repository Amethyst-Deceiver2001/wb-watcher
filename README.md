# wb-watch

A tracker for **dual-use and military-coded goods** sold on [Wildberries.ru](https://www.wildberries.ru) and the **Telegram crowdfunding** that solicits them for Russian military units.

It builds a longitudinal, queryable evidence base linking *what is sold* (listings, sellers, prices, reviews) to *who is soliciting it* (Telegram fundraising posts citing specific listings). Because Wildberries routinely edits and deletes listings and reviews, the tool snapshots over time so nothing observed is lost.

All data sources are public. Wildberries needs no authentication; Telegram uses your own Telethon API credentials against public channels. Intended for OSINT / research / sanctions-and-procurement analysis.

## What it captures

- **Items** — name, brand, subject, vendor code, full description, characteristics, certificate, WB creation date.
- **Price/stock/rating history** — one snapshot row per tracking run.
- **Sellers** — storefront name plus registered legal identity (**INN, OGRNIP, KPP**).
- **Reviews** — text, pros/cons, rating, date, reviewer country, photo/video flags, seller answers — archived before WB deletes them.
- **Telegram posts** — text, views, extracted WB/Ozon links, delivery addresses, phones, and donation details (card numbers, SBP, banks).
- **item_mentions** — the core join table linking a marketplace listing to every fundraising post that cites it.
- **tg_network_channels** — the extended network of channels referenced (textually or via forward) by scraped posts, ranked by mention count — follow-up candidates, not auto-watched.
- **"Смотрите также" (see also) graph** — one-hop expansion from every tracked item via WB's own similar-products recommendation API, capped per run.

## Install

```bash
python3 -m venv .venv
./.venv/bin/pip install -e .
cp .env.example .env      # then fill in Telegram creds (see below)
```

## Configure

- `config/seed_items.txt` — WB URLs or nm-ids to always track.
- `config/seed_sellers.txt` — WB seller URLs/ids to always fully sweep, independent of whether they already have a tracked item.
- `config/keywords.txt` — Russian discovery search terms.
- `config/channels.txt` — Telegram channel watchlist (`@handle` / `t.me/...` / numeric id).

Telegram: get `TG_API_ID` / `TG_API_HASH` from <https://my.telegram.org>, put them in `.env`, then:

```bash
wb-watch tg-login        # interactive; prints TG_SESSION=... to paste into .env
```

## Usage

```bash
wb-watch init                        # create DB, register seed items
wb-watch track --all                 # snapshot all items (card + details + reviews + seller)
wb-watch discover                    # keyword + seller-catalog + similar-items sweeps
wb-watch discover --similar-max-new 500   # widen the "see also" graph expansion cap
wb-watch tg-scan --full --days 730   # scrape channels, 2-year historical lookback
wb-watch run --full --days 730       # discover -> track -> tg-scan in one shot
wb-watch network                     # list follow-up channel candidates (not yet watched)
wb-watch export --format xlsx        # or csv / both -> exports/
wb-watch status                      # row counts per table
```

Schedule `wb-watch run` daily via cron/launchd; the snapshot history accumulates automatically.

## Data model

SQLite at `data/wb_watch.db`. Current-state tables (`items`, `sellers`, `tg_channels`) are upserted; historical tables (`item_snapshots`, `reviews`, `tg_posts`, `item_mentions`) are append-only.

## Utilities

- `scripts/tg_download_media.py` — one-off download of the media attached to
  a single public Telegram post, using the same authenticated Telethon
  session as `tg-scan` (needed when a video/file exceeds the size Telegram's
  public `?embed=1` web preview will serve, and `yt-dlp` can't extract it):
  ```bash
  .venv/bin/python scripts/tg_download_media.py https://t.me/<channel>/<msg_id>
  ```
  Not part of the `wb-watch` CLI or the tracked pipeline — a manual
  reconnaissance tool, safe to run directly per `CLAUDE.md` (single-post
  fetch, not a crawl). Saves to `<channel>_<msg_id>.<ext>` by default, with
  the extension Telethon infers from the actual media type — pass `-o` to
  pin a specific filename/extension if a downstream command needs one.

## Notes

- WB internal endpoints (card v4, search v5, feedbacks v2, similar-items) drift; versions are constants in `wb_watch/config.py`.
- Requests are globally throttled with exponential-backoff retry on 429/5xx (WB rate-limits hard).
- Ozon references found in Telegram posts are recorded in `item_mentions` (marketplace=`ozon`). Ozon's own search API is behind bot-detection (redirects to a JS challenge) and isn't scrapable the way WB's internal API is — fetching Ozon product data is out of scope; instead, item categories seen in Ozon-citing Telegram posts get added to `keywords.txt` to find WB equivalents.
- See `CLAUDE.md` for the project's exploration-vs-crawling working rules.
