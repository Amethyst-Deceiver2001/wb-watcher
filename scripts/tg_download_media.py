"""Download the media attached to a single public Telegram post.

Telegram's public web embed refuses to serve files over its size limit
("Media is too big" in the embed HTML), and yt-dlp's telegram extractor
therefore finds no video URL — so anything larger than that has to come
through an authenticated MTProto session. This reuses the project's
existing Telethon session (TG_SESSION in .env, same one `tg-scan` uses).

Single-post fetch, not a crawl: one message, one file.

    python scripts/tg_download_media.py https://t.me/<channel>/<msg_id>
    python scripts/tg_download_media.py <channel> <msg_id> -o out.mp4
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from wb_watch.tg import client as tg_client

_URL_RE = re.compile(r"t\.me/(?:s/)?([A-Za-z0-9_]+)/(\d+)")


def parse_target(args: list[str]) -> tuple[str, int]:
    if len(args) == 1:
        m = _URL_RE.search(args[0])
        if not m:
            raise SystemExit(f"cannot parse a t.me/<channel>/<id> URL from {args[0]!r}")
        return m.group(1), int(m.group(2))
    if len(args) == 2:
        return args[0].lstrip("@"), int(args[1])
    raise SystemExit("expected either a t.me URL or '<channel> <msg_id>'")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", nargs="+", help="t.me URL, or channel and message id")
    ap.add_argument("-o", "--out", help="output path (default: <channel>_<id>.<ext>)")
    ns = ap.parse_args()

    channel, msg_id = parse_target(ns.target)

    with tg_client.make_client() as client:
        msg = client.get_messages(channel, ids=msg_id)
        if msg is None:
            raise SystemExit(f"message {channel}/{msg_id} not found or not accessible")
        if not msg.media:
            raise SystemExit(f"message {channel}/{msg_id} has no media attached")

        # Report what we're about to pull before pulling it — a 6-minute video
        # is tens of MB and takes a while; better to see the size up front.
        doc = getattr(msg, "document", None)
        if doc is not None:
            print(f"media: {doc.mime_type}, {doc.size / 1e6:.1f} MB", file=sys.stderr)

        out = ns.out or f"{channel}_{msg_id}"
        path = client.download_media(msg, file=out)

    if path is None:
        raise SystemExit("download returned no path — nothing was saved")
    print(Path(path).resolve())


if __name__ == "__main__":
    main()
