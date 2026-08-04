"""Scrape watchlist channels: pull history (first run) or new messages (incremental).

Each channel is resolved, its metadata captured, and messages iterated newest-first
down to the last message id we already stored (or down to a date cutoff for the
initial 2-year historical harvest). Yields normalized post dicts with the extracted
intel (via extract.py) and, when the message is a forward, the forward source —
this is what feeds the "extended network" of channels worth following up on.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, MessageEntityTextUrl, PeerChannel

from . import extract

DEFAULT_LOOKBACK_DAYS = 730  # 2 years


def _channel_meta(entity: Any, full: Any = None) -> dict[str, Any]:
    about = None
    participants = None
    if full is not None:
        about = getattr(getattr(full, "full_chat", None), "about", None)
        participants = getattr(
            getattr(full, "full_chat", None), "participants_count", None
        )
    return {
        "id": entity.id,
        "username": getattr(entity, "username", None),
        "title": getattr(entity, "title", None),
        "about": about,
        "participants": participants,
    }


def resolve_channel(client: TelegramClient, ref: str) -> Any:
    ref = ref.strip()
    if ref.startswith("t.me/"):
        ref = "@" + ref.split("/", 1)[1]
    if ref.lstrip("-").isdigit():
        ref = int(ref)
    return client.get_entity(ref)


def _forward_info(client: TelegramClient, msg: Any) -> dict[str, Any]:
    """Resolve Telethon's fwd_from into a channel id/username/title where possible.

    fwd_from.from_id is a Peer, not a full entity — resolving it costs one API call
    per unique source, so cache misses are cheap and repeats are free within a run.
    """
    fwd = getattr(msg, "fwd_from", None)
    if fwd is None:
        return {"is_forward": False}

    out: dict[str, Any] = {
        "is_forward": True,
        "fwd_channel_id": None,
        "fwd_channel_username": None,
        "fwd_channel_title": None,
        "fwd_msg_id": getattr(fwd, "channel_post", None),
        "fwd_date": fwd.date.isoformat() if getattr(fwd, "date", None) else None,
    }
    from_id = getattr(fwd, "from_id", None)
    if isinstance(from_id, PeerChannel):
        out["fwd_channel_id"] = from_id.channel_id
        try:
            entity = client.get_entity(from_id)
            out["fwd_channel_username"] = getattr(entity, "username", None)
            out["fwd_channel_title"] = getattr(entity, "title", None)
        except Exception:
            pass
    elif getattr(fwd, "from_name", None):
        # Forward with sender info hidden — only a display name is available.
        out["fwd_channel_title"] = fwd.from_name
    return out


def _hidden_urls(msg: Any) -> list[str]:
    """URLs behind styled link text ("тут👈") — invisible in msg.message, only
    present as MessageEntityTextUrl entities. Real fundraising posts hide long
    lists of WB/Ozon links this way to keep the visible text readable; missing
    these silently drops mentions the plain-text regex scan would never see."""
    return [
        e.url for e in (getattr(msg, "entities", None) or [])
        if isinstance(e, MessageEntityTextUrl)
    ]


def iter_posts(
    client: TelegramClient,
    entity: Any,
    min_id: int = 0,
    limit: int | None = None,
    cutoff_date: datetime | None = None,
    resolve_forwards: bool = True,
) -> Iterator[dict[str, Any]]:
    """Yield posts with msg_id > min_id, newest first.

    If cutoff_date is set, stops once a message older than it is reached (Telethon
    iterates newest-to-oldest by default, so this bounds the initial historical pull).
    """
    for msg in client.iter_messages(entity, min_id=min_id, limit=limit):
        if cutoff_date is not None and msg.date is not None:
            msg_date = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
            if msg_date < cutoff_date:
                return

        text = msg.message or ""
        hidden = _hidden_urls(msg)
        extract_text = text + ("\n" + "\n".join(hidden) if hidden else "")
        intel = extract.extract(extract_text)
        fwd_info = _forward_info(client, msg) if resolve_forwards else {"is_forward": False}
        yield {
            "channel_id": entity.id,
            "msg_id": msg.id,
            "date": msg.date.isoformat() if msg.date else None,
            "text": text,
            "views": getattr(msg, "views", None),
            "forwards": getattr(msg, "forwards", None),
            "edited_date": msg.edit_date.isoformat() if msg.edit_date else None,
            **fwd_info,
            **intel,
        }


def channel_metadata(client: TelegramClient, entity: Any) -> dict[str, Any]:
    full = None
    if isinstance(entity, (Channel, Chat)):
        try:
            full = client(
                __import__(
                    "telethon.tl.functions.channels",
                    fromlist=["GetFullChannelRequest"],
                ).GetFullChannelRequest(entity)
            )
        except Exception:
            full = None
    return _channel_meta(entity, full)


def lookback_cutoff(days: int = DEFAULT_LOOKBACK_DAYS) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)
