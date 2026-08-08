"""Drive a real Chromium session across a list of Ozon URLs, recording its own
traffic as a HAR — an automated stand-in for the manual DevTools-export
workflow this project has used for Ozon so far (see har_parse.py, resolve.py).

UNTESTED whether this actually clears Ozon's bot wall. What's confirmed this
session is that a *human*-driven browser passes with zero cookies attached —
consistent with a TLS/JS-execution fingerprint check rather than a token, which
a scripted Chromium session has a real chance of also passing since Playwright
drives genuine Chromium, not a headless-only stub. But fingerprint walls this
aggressive (Ozon blocks even a bare robots.txt fetch for non-browser clients)
often specifically probe for automation markers Playwright doesn't hide by
default (navigator.webdriver, missing plugins/mimeTypes, headless-only GPU
info) — so this may get challenged or blocked outright. Treat the first run as
a proof of concept, run non-headless, and watch the window in case a manual
challenge needs solving.

Per CLAUDE.md this is a crawl job: it must be run by the user directly, never
invoked or backgrounded by the assistant, and never assumed to work until
observed.
"""
from __future__ import annotations

import random
import time
from pathlib import Path

# Deliberately imported at call time, not module load — playwright is an
# optional dependency (`pip install wb-watch[ozon-crawl] && playwright install
# chromium`), and every other module in this package must stay importable
# without it.


def crawl(
    urls: list[str],
    har_out: Path,
    profile_dir: Path,
    headless: bool = False,
    min_delay: float = 3.0,
    max_delay: float = 7.0,
) -> None:
    from playwright.sync_api import sync_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    har_out.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # launch_persistent_context (not launch() + new_context()): a real
        # profile directory that survives across runs reads as a returning
        # visitor, not a fresh-every-time automation fingerprint.
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            record_har_path=str(har_out),
            record_har_mode="full",
            viewport={"width": 1440, "height": 900},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        page = context.new_page()
        try:
            for i, url in enumerate(urls, 1):
                print(f"[{i}/{len(urls)}] {url}")
                try:
                    page.goto(url, wait_until="networkidle", timeout=45_000)
                except Exception as exc:
                    print(f"  ! navigation failed/timed out: {exc}")
                    continue

                title = page.title()
                if "antibot" in title.lower() or "доступ ограничен" in title.lower():
                    print(
                        "  ! page title suggests a challenge page — "
                        "check the browser window and solve it manually if needed"
                    )

                # Scroll a bit to trigger lazy-loaded widgets (reviews carousel,
                # recommendation rails) the way a real visitor's viewport would.
                for _ in range(3):
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(800)

                time.sleep(random.uniform(min_delay, max_delay))
        finally:
            context.close()
