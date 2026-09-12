"""
Outbound payload shape adapter (#426).

Subscribers that consume vendor-specific incoming webhook shapes
(Slack, Discord) don't want to write a per-event adapter — the
platform shapes the body to match the target. ``adapt_payload``
returns the JSON dict that should be POSTed to the subscriber URL,
preserving the original Astrolift envelope under ``meta`` so
operators who later switch back to ``generic`` don't lose data.

Format choices:
* ``generic`` (default) — the raw Astrolift envelope. Unchanged.
* ``slack``   — Slack incoming-webhook shape (``text`` + ``attachments``).
* ``discord`` — Discord webhook shape (``content`` + ``embeds``).

Adapters are intentionally simple. The goal is "operator wires
the URL and sees a sane message in their channel". Subscribers
who need richer formatting either pick ``generic`` and template
on their side, or open a follow-up for additional presets.
"""

from __future__ import annotations

from typing import Any

GENERIC = "generic"
SLACK = "slack"
DISCORD = "discord"

VALID_FORMATS = frozenset({GENERIC, SLACK, DISCORD})


def adapt_payload(*, format: str, envelope: dict[str, Any]) -> dict[str, Any]:
    """Shape ``envelope`` to ``format``.

    ``envelope`` is the canonical Astrolift event body
    (``{"kind": ..., "subscription_id": ..., ...}``). The return
    value is the JSON dict that should be serialised + POSTed.
    Unknown formats fall back to ``generic`` rather than raising:
    the delivery loop should never crash because of a stale enum
    in the DB.
    """
    if format == SLACK:
        return _adapt_slack(envelope)
    if format == DISCORD:
        return _adapt_discord(envelope)
    from astrolift_operations.zentinelle_bridge import is_zentinelle_envelope

    if is_zentinelle_envelope(envelope.get("event_type", ""), envelope.get("payload")):
        # Zentinelle's collector validates the locked envelope at the top
        # level, so an ``AUDIT.*`` event posts its inner envelope, not the
        # Astrolift wrapper around it.
        return dict(envelope["payload"])
    return dict(envelope)


def _summary_line(envelope: dict[str, Any]) -> str:
    """One-line human summary. Both Slack and Discord lead with
    the event kind so the channel post is readable without
    expanding the payload."""
    kind = str(envelope.get("kind") or envelope.get("event_type") or "astrolift.event")
    message = envelope.get("message")
    if message:
        return f"*{kind}* — {message}"
    return f"*{kind}*"


def _flatten_for_kv(envelope: dict[str, Any]) -> list[tuple[str, str]]:
    """Render scalar envelope fields as (label, value) pairs for
    Slack/Discord block content. Nested objects collapse to JSON
    so we don't drop information the operator wired up. ``message``
    + ``kind`` are intentionally skipped — they already lead the
    summary."""
    import json

    skip = {"message", "kind", "event_type"}
    out: list[tuple[str, str]] = []
    for key in sorted(envelope.keys()):
        if key in skip:
            continue
        value = envelope[key]
        if value is None:
            continue
        if isinstance(value, str | int | float | bool):
            out.append((key, str(value)))
        else:
            try:
                out.append((key, json.dumps(value, default=str)[:256]))
            except (TypeError, ValueError):
                out.append((key, repr(value)[:256]))
    return out


def _adapt_slack(envelope: dict[str, Any]) -> dict[str, Any]:
    """Slack incoming-webhook shape. ``text`` is the fallback
    line that mobile / push notifications show; ``attachments``
    carries the structured detail."""
    summary = _summary_line(envelope)
    fields = [{"title": label, "value": value, "short": True} for label, value in _flatten_for_kv(envelope)]
    attachment: dict[str, Any] = {
        "color": "#0EA5E9",  # astrolift teal — readable on light + dark
        "fallback": summary,
    }
    if fields:
        attachment["fields"] = fields
    return {
        "text": summary,
        "attachments": [attachment],
        "meta": dict(envelope),
    }


def _adapt_discord(envelope: dict[str, Any]) -> dict[str, Any]:
    """Discord webhook shape. ``content`` is the leading text;
    ``embeds`` is the structured panel."""
    summary = _summary_line(envelope)
    embed_fields = [
        {"name": label, "value": value, "inline": True} for label, value in _flatten_for_kv(envelope)
    ]
    embed: dict[str, Any] = {
        "title": str(envelope.get("kind") or "astrolift.event"),
        "color": 0x0EA5E9,
        "description": str(envelope.get("message") or ""),
    }
    if embed_fields:
        embed["fields"] = embed_fields
    return {
        "content": summary,
        "embeds": [embed],
        "meta": dict(envelope),
    }
