"""Shared HTTP client for all Wildberries calls.

WB's internal endpoints gzip responses and rate-limit aggressively (429s are common,
especially the seller-catalog host). Everything funnels through here so throttling,
retry/backoff, and User-Agent rotation apply uniformly.

httpx handles gzip transparently. tenacity handles retry with exponential backoff +
jitter on 429/5xx. A simple monotonic token-bucket enforces a global min-interval
between requests.
"""
from __future__ import annotations

import itertools
import subprocess
import threading
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from . import config

# Rotate through a few realistic desktop UAs to look less like a single scraper.
_USER_AGENTS = itertools.cycle(
    [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ]
)


class RetryableStatus(Exception):
    """Raised for 429/5xx so tenacity retries; carries the status for logging."""

    def __init__(self, status: int, url: str):
        self.status = status
        self.url = url
        super().__init__(f"HTTP {status} for {url}")


class _Throttle:
    """Global minimum-interval gate shared across threads."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


_throttle = _Throttle(config.WB_MIN_INTERVAL)
# Ozon's redirect endpoint has a much tighter burst-rate limit than WB (see
# get_redirect_location) — deliberately separate so a WB run doesn't inherit
# Ozon's slower pace, and vice versa.
_ozon_throttle = _Throttle(config.OZON_MIN_INTERVAL)
# list-org.com (vendor registry lookups) has its own tight burst-rate defense
# (see vendors/lookup.py) — separate throttle, same reasoning as Ozon's.
_vendor_throttle = _Throttle(config.VENDOR_LOOKUP_MIN_INTERVAL)

_client = httpx.Client(
    http2=False,
    timeout=httpx.Timeout(25.0, connect=15.0),
    follow_redirects=True,
    headers={
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    },
)


@retry(
    retry=retry_if_exception_type((RetryableStatus, httpx.TransportError)),
    stop=stop_after_attempt(config.WB_MAX_RETRIES),
    wait=wait_exponential_jitter(initial=1.0, max=60.0),
    reraise=True,
)
def _request(url: str, params: dict[str, Any] | None,
             follow_redirects: bool | None = None) -> httpx.Response:
    _throttle.wait()
    kwargs: dict[str, Any] = {"params": params, "headers": {"User-Agent": next(_USER_AGENTS)}}
    if follow_redirects is not None:
        kwargs["follow_redirects"] = follow_redirects
    resp = _client.get(url, **kwargs)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RetryableStatus(resp.status_code, str(resp.url))
    return resp


def get_json(url: str, params: dict[str, Any] | None = None) -> Any | None:
    """GET and parse JSON. Returns None on 404 or non-JSON body (both mean 'no data')."""
    try:
        resp = _request(url, params)
    except RetryableStatus as exc:
        # Exhausted retries on a persistently rate-limited/erroring host.
        raise RuntimeError(f"WB request failed after retries: {exc}") from exc

    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    text = resp.text.strip()
    if not text:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def get_bytes(url: str) -> bytes | None:
    """GET raw bytes (product images). Returns None on 404 or any failure —
    a missing gallery image is routine (fewer pics than the card's max index
    suggests) and shouldn't abort the caller's OCR loop."""
    try:
        resp = _request(url, params=None)
    except RetryableStatus:
        return None
    if resp.status_code != 200:
        return None
    return resp.content


def get_redirect_location(url: str) -> str | None:
    """Resolve a redirect's Location header via the system `curl` binary.

    Confirmed live, side by side at the same instant: plain `curl` reliably gets a
    301 from Ozon's short-link redirector while httpx (both the shared client and a
    bare stateless `httpx.get`) gets 403'd on the identical URL — a TLS/JA3
    fingerprint difference (curl's handshake isn't flagged, Python's TLS stack is),
    not a cookie or rate-limiting issue as first suspected. No amount of
    header-tweaking fixes a fingerprint mismatch, so this shells out to curl
    specifically for this one call site rather than fighting httpx's TLS layer.
    """
    _ozon_throttle.wait()
    try:
        proc = subprocess.run(
            [
                "curl", "-s", "-o", "/dev/null", "--max-time", "15",
                "-A", next(_USER_AGENTS),
                "-w", "%{http_code}|%{redirect_url}",
                url,
            ],
            capture_output=True, text=True, timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if "|" not in proc.stdout:
        return None
    code, _, location = proc.stdout.partition("|")
    if code.strip().startswith("3") and location:
        return location
    return None


def vendor_throttle_wait() -> None:
    """Pace list-org.com requests (see vendors/lookup.py) — its own tight
    burst-rate defense, separate from WB's and Ozon's throttles."""
    _vendor_throttle.wait()


def head_ok(url: str) -> bool:
    """Cheap existence probe used for basket-host resolution."""
    _throttle.wait()
    try:
        resp = _client.get(url, headers={"User-Agent": next(_USER_AGENTS)})
    except httpx.TransportError:
        return False
    return resp.status_code == 200


def close() -> None:
    _client.close()
