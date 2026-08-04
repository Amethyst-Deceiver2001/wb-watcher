"""wb-watch command-line interface."""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from . import config, db
from .export import csv_xlsx
from .pipeline import backfill as backfill_mod
from .pipeline import discover as discover_mod
from .pipeline import match as match_mod
from .analysis import patterns as patterns_mod
from .pipeline import lookup_vendors as lookup_vendors_mod
from .pipeline import resolve_ozon as resolve_ozon_mod
from .pipeline import scan_telegram, track_items
from .pipeline import news_scan as news_scan_mod
from .site import update_news_ticker

app = typer.Typer(add_completion=False, help="Tracker for dual-use goods on Wildberries + linked Telegram crowdfunding.")
console = Console()


def _conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


@app.command()
def init():
    """Create the database and load seed items from config/seed_items.txt."""
    conn = _conn()
    if not config.SEED_ITEMS_FILE.exists():
        console.print(f"[red]missing {config.SEED_ITEMS_FILE}[/]")
        raise typer.Exit(1)
    loaded = 0
    for line in config.SEED_ITEMS_FILE.read_text(encoding="utf-8").splitlines():
        nm_id = track_items.parse_seed_line(line)
        if nm_id and not db.item_exists(conn, nm_id):
            # Insert a bare shell; `track` fills it in. Marks source=seed.
            db.upsert_item(conn, {"nm_id": nm_id, "source": "seed"})
            loaded += 1
    console.print(f"[green]init complete[/] — {loaded} new seed items registered "
                  f"(db: {config.DB_PATH})")


@app.command()
def track(
    nm: int = typer.Option(None, help="Track a single nm-id"),
    all: bool = typer.Option(False, "--all", help="Track every registered item"),
):
    """Snapshot tracked items (card + details + reviews + seller)."""
    conn = _conn()
    if nm:
        track_items.track_item(conn, nm, source="seed")
    elif all:
        results = track_items.track_all(conn)
        ok = sum(1 for r in results if r["ok"])
        added = sum(r["reviews_added"] for r in results)
        console.print(f"[green]tracked {ok}/{len(results)} items, +{added} reviews[/]")
    else:
        console.print("[yellow]specify --nm ID or --all[/]")
        raise typer.Exit(1)


@app.command("scan-images")
def scan_images(
    limit: int = typer.Option(None, help="Stop after this many items (omit for all)"),
):
    """Backfill gallery-image OCR over already-tracked items (new tracks get
    this automatically). Resumable — skips items already analyzed, so it's
    safe to stop and rerun."""
    conn = _conn()
    res = track_items.scan_images(conn, limit=limit)
    console.print(
        f"[green]scan-images:[/] {res['processed']} processed, "
        f"{res['skipped']} already done, {res['signals']} images with "
        f"combat-context signal"
    )


@app.command()
def discover(
    sellers: bool = typer.Option(
        True, help="Also sweep seeded vendors' catalogs (config/seed_sellers.txt)"
    ),
):
    """Run mentions + keyword + seller-catalog sweeps to find new listings."""
    conn = _conn()
    res = discover_mod.run(conn, sellers=sellers)
    men, kw, sl = res["mentions"], res["keyword"], res["seller"]
    console.print(
        f"[green]discovery:[/] +{men['added']} by telegram mention ({men['failed']} failed), "
        f"+{kw['added']} by keyword ({kw['failed']} failed), "
        f"+{sl['added']} by seller ({sl['failed']} failed)"
    )


@app.command("tg-login")
def tg_login():
    """One-time Telethon auth. Prints a session string to paste into .env."""
    from .tg import client as tg_client
    try:
        session = tg_client.login()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    console.print("\n[bold green]Add this line to your .env:[/]")
    console.print(f"TG_SESSION={session}")


@app.command("tg-scan")
def tg_scan(
    full: bool = typer.Option(False, "--full", help="Pull full history (first run)"),
    days: int = typer.Option(
        730, help="With --full, stop this many days back (default: 2 years)"
    ),
):
    """Scrape watchlist channels and link WB/Ozon mentions to items."""
    conn = _conn()
    try:
        totals = scan_telegram.scan(conn, full_history=full, lookback_days=days)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    console.print(f"[green]tg-scan:[/] {totals['posts']} posts, "
                  f"{totals['mentions']} mentions, {totals['new_items']} new items")


@app.command()
def rematch():
    """Rebuild item_mentions from stored Telegram posts (no network)."""
    conn = _conn()
    n = match_mod.rematch(conn)
    console.print(f"[green]rematch:[/] {n} mentions re-derived")


@app.command()
def backfill():
    """Recompute category/seller-classification/review-signals on existing rows
    (no network — safe to run any time the analysis rules change)."""
    conn = _conn()
    res = backfill_mod.run(conn)
    console.print(
        f"[green]backfill:[/] {res['items']} items categorized, "
        f"military_class={res['military_class']}, "
        f"wb_policy={res['wb_policy']}, "
        f"delivery_source={res['delivery_source']}, "
        f"{res['sellers']} sellers classified, "
        f"{res['field_use_reviews']} reviews flagged with field-use signal, "
        f"{res['image_signals']} scanned images flagged with field-use signal"
    )


@app.command("news-scan")
def news_scan():
    """Fetch Meduza/Mediazona/Kommersant/RBC RSS, store new Wildberries/Kim
    headlines for the docs-site news ticker. Re-run periodically to keep it
    current (see wb_watch/site/build_assets.py's build_news_ticker_html)."""
    n = news_scan_mod.run()
    console.print(f"[green]news-scan:[/] {n} new headlines stored")


@app.command("update-news-ticker")
def update_news_ticker_cmd(
    top: int = typer.Option(6, help="How many headlines to show in the ticker"),
    scan: bool = typer.Option(True, help="Run news-scan first to fetch fresh headlines"),
):
    """One-shot refresh: scan RSS feeds, then splice the ticker fragment
    straight into docs/index.html (replaces the <div class="newsbox ui">
    block in place)."""
    res = update_news_ticker.run(top=top, do_scan=scan)
    console.print(
        f"[green]update-news-ticker:[/] {res['scanned_new']} new headlines "
        f"scanned, {res['ticker_items']} shown, docs/index.html updated"
    )


@app.command("resolve-ozon")
def resolve_ozon(
    limit: int = typer.Option(
        None, help="Cap on how many codes to attempt this run (default: all)"
    ),
):
    """Resolve unresolved Ozon short links to product identity (slow, rate-limited —
    see ozon/resolve.py for why). Safe to re-run; skips already-resolved codes."""
    conn = _conn()
    res = resolve_ozon_mod.run(conn, limit=limit)
    console.print(f"[green]resolve-ozon:[/] {res['resolved']}/{res['total']} resolved, "
                  f"{res['failed']} failed")


@app.command("lookup-vendors")
def lookup_vendors(
    limit: int = typer.Option(
        None, help="Cap on how many INNs to attempt this run (default: all)"
    ),
):
    """Resolve director/founders/registration details for legal-entity sellers via
    list-org.com (slow, rate-limited). Safe to re-run; skips already-resolved INNs."""
    conn = _conn()
    res = lookup_vendors_mod.run(conn, limit=limit)
    console.print(f"[green]lookup-vendors:[/] {res['resolved']}/{res['total']} "
                  f"resolved, {res['failed']} failed")


@app.command()
def patterns():
    """Report co-occurrence, temporal trends, recurring campaigns, and quantity
    signals from the Telegram corpus (no network — see export for the full sheets)."""
    conn = _conn()
    res = patterns_mod.run_all(conn)

    console.print("\n[bold]Top category co-occurrence (same post):[/]")
    for row in res["category_cooccurrence"][:10]:
        console.print(f"  {row['category_a']} + {row['category_b']}: "
                       f"{row['co_occurring_posts']} posts")

    console.print("\n[bold]Recurring campaigns (payment detail reused across "
                  "channels):[/]")
    for c in res["recurring_campaigns"][:10]:
        console.print(f"  {c['payment_detail']}: {c['distinct_channels']} channels, "
                       f"{c['occurrences']}x, {c['first_seen']} -> {c['last_seen']}")

    console.print(f"\n[bold]Quantity-bearing mentions:[/] {len(res['quantities'])} "
                  f"(top by max quantity):")
    for q in res["quantities"][:10]:
        console.print(f"  {q['category']} ({q['marketplace']}:{q['external_id']}): "
                       f"{q['quantities_mentioned']}")

    months = sorted({r["month"] for r in res["temporal_trends"]})
    console.print(f"\n[bold]Temporal trends:[/] {len(months)} months covered "
                  f"({months[0] if months else '—'} to {months[-1] if months else '—'}) "
                  f"— full breakdown in export sheet 'temporal_trends'")


@app.command()
def run(
    full: bool = typer.Option(False, "--full", help="Pull full Telegram history"),
    days: int = typer.Option(730, help="With --full, lookback window in days"),
):
    """Full cycle: discover -> track -> tg-scan."""
    conn = _conn()
    console.print("[bold]== discover ==[/]")
    discover_mod.run(conn, sellers=True)
    console.print("[bold]== track ==[/]")
    track_items.track_all(conn)
    console.print("[bold]== tg-scan ==[/]")
    try:
        scan_telegram.scan(conn, full_history=full, lookback_days=days)
    except RuntimeError as exc:
        console.print(f"[yellow]telegram skipped: {exc}[/]")


@app.command()
def export(
    format: str = typer.Option("xlsx", help="csv | xlsx | both"),
):
    """Export tables + joined views to the exports/ directory."""
    conn = _conn()
    if format in ("csv", "both"):
        paths = csv_xlsx.export_csv(conn)
        console.print(f"[green]csv:[/] {len(paths)} files -> {config.EXPORT_DIR}")
    if format in ("xlsx", "both"):
        path = csv_xlsx.export_xlsx(conn)
        console.print(f"[green]xlsx:[/] {path}")


@app.command()
def status():
    """Show row counts across all tables."""
    conn = _conn()
    table = Table(title="wb-watch status")
    table.add_column("table")
    table.add_column("rows", justify="right")
    for t in ("items", "item_snapshots", "sellers", "reviews",
              "tg_channels", "tg_posts", "item_mentions", "tg_network_channels"):
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        table.add_row(t, str(n))
    console.print(table)


@app.command()
def network(top: int = typer.Option(30, help="How many candidates to show")):
    """List the extended channel network — follow-up candidates not yet watched."""
    conn = _conn()
    table = Table(title="Follow-up channel candidates (not in watchlist)")
    table.add_column("handle")
    table.add_column("title")
    table.add_column("mentions", justify="right")
    table.add_column("via")
    rows = conn.execute(
        """SELECT handle, title, mention_count, discovered_via FROM tg_network_channels
           WHERE in_watchlist = 0 ORDER BY mention_count DESC LIMIT ?""",
        (top,),
    ).fetchall()
    for r in rows:
        table.add_row(f"@{r['handle']}", r["title"] or "", str(r["mention_count"]),
                      r["discovered_via"])
    console.print(table)
    if not rows:
        console.print("[yellow]No follow-up candidates yet — run tg-scan first.[/]")


if __name__ == "__main__":
    app()
