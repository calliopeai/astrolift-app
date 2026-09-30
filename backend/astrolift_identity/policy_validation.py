"""Validate policy JSON against the evaluator's typed condition catalog."""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from astrolift_identity.abac import ACTOR_KEYS, CONDITION_KINDS, RESOURCE_KEYS, _parse_hhmm


def validate_policy_shape(*, effect: str, conditions: Any, resource_pattern: Any, actor_pattern: Any):
    """Return the first (field, message), or None. Never silently coerce JSON."""
    if effect not in {"ALLOW", "DENY"}:
        return "effect", "effect must be ALLOW or DENY"
    for field, pattern, catalog in (
        ("resourcePattern", resource_pattern, RESOURCE_KEYS),
        ("actorPattern", actor_pattern, ACTOR_KEYS),
    ):
        if not isinstance(pattern, dict):
            return field, "pattern must be an object"
        known = {entry.key for entry in catalog}
        for key, raw in pattern.items():
            if key not in known:
                return field, f"unknown pattern key {key!r}"
            values = [raw] if isinstance(raw, str) else raw
            if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
                return field, f"{key} must be a string or a list of nonempty strings"
    if not isinstance(conditions, list):
        return "conditions", "conditions must be a list"
    kinds = {kind.kind: kind for kind in CONDITION_KINDS}
    for index, condition in enumerate(conditions):
        path = f"conditions[{index}]"
        if not isinstance(condition, dict):
            return path, "condition must be an object"
        kind = kinds.get(condition.get("kind")) if isinstance(condition.get("kind"), str) else None
        if kind is None:
            return path, "unknown condition kind"
        fields = {field.name for field in kind.fields}
        if set(condition) - fields - {"kind"}:
            return path, "condition contains unknown fields"
        for field in kind.fields:
            field_path = f"{path}.{field.name}"
            if field.name not in condition:
                if field.required:
                    return field_path, "field is required"
                continue
            value = condition[field.name]
            if field.type == "integer":
                if not isinstance(value, int) or isinstance(value, bool) or value < (field.minimum or 1):
                    return field_path, f"must be an integer of at least {field.minimum or 1}"
            elif field.type == "time_zone":
                try:
                    if not isinstance(value, str) or not value:
                        raise ValueError("empty time zone")
                    ZoneInfo(value)
                except (ValueError, ZoneInfoNotFoundError):
                    return field_path, "must be an IANA time zone"
            else:
                if (
                    not isinstance(value, list)
                    or not value
                    or not all(isinstance(v, str) and v.strip() for v in value)
                ):
                    return field_path, "must be a nonempty list of strings"
                for item in value:
                    if field.type == "weekdays" and item.lower() not in field.options:
                        return field_path, "contains an unknown weekday"
                    if field.type == "cidrs":
                        try:
                            ipaddress.ip_network(item.strip(), strict=False)
                        except ValueError:
                            return field_path, "contains an invalid IP range"
                    if field.type == "time_ranges":
                        try:
                            if not re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", item):
                                raise ValueError("bad time range")
                            start, stop = item.split("-")
                            if _parse_hhmm(start, end=False) == _parse_hhmm(stop, end=True):
                                raise ValueError("empty range")
                        except ValueError:
                            return field_path, "must contain nonempty HH:MM-HH:MM ranges"
    return None
