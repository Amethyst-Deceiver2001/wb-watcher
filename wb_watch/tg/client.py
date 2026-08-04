"""Telethon client bootstrap using a persisted StringSession.

The session string lives in .env (TG_SESSION) so subsequent runs are non-interactive.
`login()` is the one-time interactive flow that prints the string to paste into .env.
"""
from __future__ import annotations

# Telethon's client methods (get_entity, iter_messages, client(request), ...) are
# async coroutines by default. Importing telethon.sync patches TelegramClient so
# they can be called synchronously (each call runs its own event loop under the
# hood) — required since the rest of this codebase calls them without `await`.
# Must be imported before any TelegramClient method is invoked anywhere.
import telethon.sync  # noqa: F401
from telethon import TelegramClient
from telethon.sessions import StringSession

from .. import config


def _require_creds() -> tuple[int, str]:
    if not config.TG_API_ID or not config.TG_API_HASH:
        raise RuntimeError(
            "TG_API_ID / TG_API_HASH not set. Add them to .env "
            "(get them from https://my.telegram.org)."
        )
    return int(config.TG_API_ID), config.TG_API_HASH


def make_client() -> TelegramClient:
    """Client for non-interactive runs; requires TG_SESSION in .env."""
    api_id, api_hash = _require_creds()
    if not config.TG_SESSION:
        raise RuntimeError(
            "TG_SESSION not set. Run `wb-watch tg-login` once and paste the "
            "printed session string into .env as TG_SESSION."
        )
    return TelegramClient(StringSession(config.TG_SESSION), api_id, api_hash)


def login() -> str:
    """Interactive one-time auth. Returns the StringSession to store in .env."""
    api_id, api_hash = _require_creds()
    with TelegramClient(StringSession(), api_id, api_hash) as client:
        client.start()  # prompts for phone + code (+ 2FA) on the console
        return client.session.save()
