"""Bulk-resolve Ozon short links cited in Telegram posts to product identity.

Each call hits Ozon's short-link redirector (see ozon/resolve.py, http.py) which is
tightly rate-limited — this loop is intentionally slow (one request per
config.OZON_MIN_INTERVAL, currently 8s) and expected to have a real failure rate.
Never run to completion inline; this is a crawl job per CLAUDE.md, always handed to
the user as a CLI command.
"""
from __future__ import annotations

import sqlite3

from rich.console import Console

from .. import db
from ..ozon import resolve as ozon_resolve

console = Console()


def run(conn: sqlite3.Connection, limit: int | None = None) -> dict[str, int]:
    codes = db.unresolved_ozon_codes(conn)
    if limit:
        codes = codes[:limit]

    resolved = 0
    failed = 0
    for i, code in enumerate(codes, 1):
        item = ozon_resolve.resolve_short_code(code)
        if item:
            db.upsert_ozon_item(conn, item)
            resolved += 1
            console.print(f"[green][{i}/{len(codes)}][/] {code} -> {item['slug_text']}")
        else:
            failed += 1
            console.print(f"[yellow][{i}/{len(codes)}][/] {code} -> unresolved")

    console.print(f"[cyan]resolve-ozon:[/] {resolved} resolved, {failed} failed "
                  f"of {len(codes)} codes")
    return {"resolved": resolved, "failed": failed, "total": len(codes)}
