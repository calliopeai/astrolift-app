"""Validation for the operator UI's appearance axes (#135).

The axis vocabulary is defined by the client (``frontend/lib/appearance.ts``),
but an org-level *default* is persisted, so it has to be validated here — the
client's own ``normalize`` protects the client, not the database, and an org
admin posting the mutation directly is not obliged to be a browser.

Kept deliberately narrow: reject unknown keys and unknown values with a
message naming what was allowed, rather than silently dropping them. A
silently-dropped axis is the "I set it and nothing happened" bug this whole
issue exists to stop repeating.

If a new axis or value lands in the client, it has to land here too. That
duplication is the price of validating server-side; the alternative — trusting
the client — is how you get unbounded strings in a JSON column.
"""

from __future__ import annotations

from typing import Any

GROUNDS = frozenset({"black", "charcoal", "emerald", "paper", "mist"})
ACCENTS = frozenset({"green", "copper", "ice", "periwinkle", "amber"})
DENSITIES = frozenset({"compact", "cards"})
CORNERS = frozenset({0, 2, 4, 10})

_ALLOWED: dict[str, frozenset] = {
    "ground": GROUNDS,
    "accent": ACCENTS,
    "density": DENSITIES,
    "corners": CORNERS,
}


class AppearanceError(ValueError):
    """Raised when an appearance payload is not a valid partial preference."""


def validate_appearance(raw: Any) -> dict[str, Any]:
    """Return a clean partial appearance dict, or raise :class:`AppearanceError`.

    Every key is optional — an org may pin only the accent and leave the rest
    to each person. An empty mapping is valid and means "no house theme".
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AppearanceError("appearance must be an object")

    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        allowed = _ALLOWED.get(key)
        if allowed is None:
            raise AppearanceError(f"unknown appearance key {key!r}; allowed: {', '.join(sorted(_ALLOWED))}")
        # bool is an int subclass, and True would otherwise sneak past the
        # corners check as 1 — reject it explicitly.
        if key == "corners" and isinstance(value, bool):
            raise AppearanceError("corners must be a number, not a boolean")
        if value not in allowed:
            allowed_str = ", ".join(str(v) for v in sorted(allowed, key=str))
            raise AppearanceError(f"invalid {key} {value!r}; allowed: {allowed_str}")
        cleaned[key] = value
    return cleaned
