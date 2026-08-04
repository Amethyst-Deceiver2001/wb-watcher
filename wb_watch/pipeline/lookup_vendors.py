"""Bulk-resolve director/founders/registration details for legal-entity vendors via
list-org.com (see vendors/lookup.py).

Tightly rate-limited (see config.VENDOR_LOOKUP_MIN_INTERVAL) — this loop is
intentionally slow and expected to have a real failure rate. Never run to
completion inline; this is a crawl job per CLAUDE.md, always handed to the user as
a CLI command.
"""
from __future__ import annotations

import sqlite3

from rich.console import Console

from .. import db
from ..vendors import lookup as vendor_lookup

console = Console()


def run(conn: sqlite3.Connection, limit: int | None = None) -> dict[str, int]:
    inns = db.unresolved_vendor_inns(conn)
    if limit:
        inns = inns[:limit]

    resolved = 0
    failed = 0
    for i, inn in enumerate(inns, 1):
        data = vendor_lookup.lookup_vendor(inn)
        if data:
            db.upsert_vendor_details(conn, data)
            resolved += 1
            console.print(f"[green][{i}/{len(inns)}][/] {inn} -> "
                          f"{data.get('director_name') or '(no director listed)'}")
        else:
            failed += 1
            console.print(f"[yellow][{i}/{len(inns)}][/] {inn} -> unresolved")

    console.print(f"[cyan]lookup-vendors:[/] {resolved} resolved, {failed} failed "
                  f"of {len(inns)} INNs")
    return {"resolved": resolved, "failed": failed, "total": len(inns)}
