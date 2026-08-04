"""Scan the Telegram watchlist, store posts, and record marketplace mentions.

For each channel in config/channels.txt: resolve it, capture metadata, pull messages
newer than the last stored id (or back to a date cutoff on the initial historical
pull), persist each post with its extracted intel, and write item_mention rows. Any
WB nm-id cited by a post that we don't yet track is added and fully tracked
(source='telegram') — a listing a fundraiser links to becomes monitored.

Every post also feeds the "extended network": textual t.me/@handle references and
structurally-resolved forward sources are tallied in tg_network_channels as follow-up
candidates, without auto-adding them to the watchlist.
"""
from __future__ import annotations

import sqlite3

from rich.console import Console

from .. import config, db
from ..tg import client as tg_client
from ..tg import scraper
from . import track_items

console = Console()


def _load_channels() -> list[str]:
    if not config.CHANNELS_FILE.exists():
        return []
    lines = config.CHANNELS_FILE.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def _record_network(conn: sqlite3.Connection, post: dict) -> None:
    for handle in post.get("referenced_channels", []):
        db.upsert_network_channel(conn, handle, discovered_via="text_ref")
    if post.get("is_forward") and post.get("fwd_channel_username"):
        db.upsert_network_channel(
            conn,
            post["fwd_channel_username"],
            discovered_via="forward",
            title=post.get("fwd_channel_title"),
            channel_id=post.get("fwd_channel_id"),
        )


def scan(conn: sqlite3.Connection, full_history: bool = False,
         lookback_days: int | None = None, track_new: bool = True) -> dict[str, int]:
    channels = _load_channels()
    if not channels:
        console.print("[yellow]No channels in config/channels.txt[/]")
        return {"posts": 0, "mentions": 0, "new_items": 0}

    db.mark_watchlist_channels(conn, channels)

    totals = {"posts": 0, "mentions": 0, "new_items": 0}
    cutoff = scraper.lookback_cutoff(lookback_days) if (full_history and lookback_days) else None

    client = tg_client.make_client()
    with client:
        for ref in channels:
            try:
                entity = scraper.resolve_channel(client, ref)
            except Exception as exc:  # noqa: BLE001 - report and continue
                console.print(f"[red]cannot resolve {ref}: {exc}[/]")
                continue

            meta = scraper.channel_metadata(client, entity)
            min_id = 0 if full_history else db.channel_last_msg_id(conn, entity.id)
            console.print(
                f"[cyan]channel[/] {meta.get('title') or ref} "
                f"(id {entity.id}) from msg_id>{min_id}"
                + (f", back to {cutoff.date()}" if cutoff else "")
            )

            max_seen = min_id
            for post in scraper.iter_posts(client, entity, min_id=min_id, cutoff_date=cutoff):
                db.add_post(conn, post)
                _record_network(conn, post)
                totals["posts"] += 1
                max_seen = max(max_seen, post["msg_id"])

                for nm_id in post["wb_nm_ids"]:
                    if track_new and not db.item_exists(conn, nm_id):
                        track_items.track_item(conn, nm_id, source="telegram")
                        totals["new_items"] += 1
                    db.add_mention(conn, {
                        "nm_id": nm_id,
                        "marketplace": "wb",
                        "external_id": str(nm_id),
                        "tg_post_id": f"{post['channel_id']}:{post['msg_id']}",
                        "channel_id": post["channel_id"],
                        "matched_url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                    })
                    totals["mentions"] += 1

                for oz in post["ozon_ids"]:
                    db.add_mention(conn, {
                        "nm_id": None,
                        "marketplace": "ozon",
                        "external_id": oz,
                        "tg_post_id": f"{post['channel_id']}:{post['msg_id']}",
                        "channel_id": post["channel_id"],
                        "matched_url": f"https://www.ozon.ru/product/{oz}",
                    })
                    totals["mentions"] += 1

            meta["last_msg_id"] = max_seen
            db.upsert_channel(conn, meta)

    # Re-mark after the loop: watchlist channels only get their tg_network_channels
    # row created during _record_network() above (when referenced/forwarded by some
    # post), so marking only *before* the loop would miss every one of them on a
    # first-ever scan — they'd wrongly show up as "not in watchlist" follow-up
    # candidates despite being actively scraped.
    db.mark_watchlist_channels(conn, channels)
    return totals
