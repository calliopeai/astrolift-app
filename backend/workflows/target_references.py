"""Explicit GUID references retain their identity without slug substitution."""

from __future__ import annotations

import uuid

from django.db.models import Q


def reference_guid(value: str) -> str | None:
    if not value.startswith("guid:"):
        return None
    raw = value.removeprefix("guid:")
    try:
        canonical = str(uuid.UUID(raw))
    except (ValueError, AttributeError) as exc:
        raise ValueError("guid: references require a canonical UUID") from exc
    if raw != canonical:
        raise ValueError("guid: references require a canonical UUID")
    return canonical


def reference_matches(candidate, value: str) -> bool:
    try:
        guid = reference_guid(value)
    except ValueError:
        return False
    return str(candidate.guid) == guid if guid is not None else candidate.slug == value


def references_filter(values) -> Q:
    slugs, guids = set(), set()
    for value in values:
        try:
            guid = reference_guid(value)
        except ValueError:
            continue
        if guid is None:
            slugs.add(value)
        else:
            guids.add(guid)
    return Q(slug__in=slugs) | Q(guid__in=guids)
