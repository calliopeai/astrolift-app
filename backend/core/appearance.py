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

import re
from typing import Any

GROUNDS = frozenset({"black", "charcoal", "emerald", "paper", "mist"})
ACCENTS = frozenset({"green", "copper", "ice", "periwinkle", "amber"})
DENSITIES = frozenset({"compact", "cards"})
CORNERS = frozenset({0, 2, 4, 10})

# Palette option A: any colour as the accent, as lowercase ``#rrggbb``. It has
# to stand 3:1 off the ground it sits on (WCAG 2.2 §1.4.11), measured against
# the same ground colours the client uses (GROUND_BACKGROUND in
# frontend/lib/appearance.ts). When an org pins an accent but not a ground,
# each person's ground varies, so the client re-checks and falls back there.
CUSTOM_ACCENT = re.compile(r"^#[0-9a-f]{6}$")
MIN_ACCENT_CONTRAST = 3.0
GROUND_BACKGROUND = {
    "black": "#000000",
    "charcoal": "#0e0f10",
    "emerald": "#050b08",
    "paper": "#faf6ee",
    "mist": "#f2f7f9",
}

_ALLOWED: dict[str, frozenset] = {
    "ground": GROUNDS,
    "accent": ACCENTS,
    "density": DENSITIES,
    "corners": CORNERS,
}


class AppearanceError(ValueError):
    """Raised when an appearance payload is not a valid partial preference."""


def _luminance(hex_colour: str) -> float:
    n = int(hex_colour[1:], 16)
    channels = [((n >> shift) & 255) / 255 for shift in (16, 8, 0)]
    r, g, b = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """The WCAG contrast ratio between two ``#rrggbb`` colours, 1 to 21."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


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
            raise AppearanceError(
                f"unknown appearance key {key!r}; allowed: {', '.join(sorted(_ALLOWED))}"
            )
        # bool is an int subclass, and True would otherwise sneak past the
        # corners check as 1 — reject it explicitly.
        if key == "corners" and isinstance(value, bool):
            raise AppearanceError("corners must be a number, not a boolean")
        if key == "accent" and isinstance(value, str) and CUSTOM_ACCENT.match(value):
            cleaned[key] = value
            continue
        if value not in allowed:
            allowed_str = ", ".join(str(v) for v in sorted(allowed, key=str))
            if key == "accent":
                allowed_str += ", or a custom colour as lowercase #rrggbb"
            raise AppearanceError(f"invalid {key} {value!r}; allowed: {allowed_str}")
        cleaned[key] = value

    accent, ground = cleaned.get("accent"), cleaned.get("ground")
    if isinstance(accent, str) and accent.startswith("#") and ground is not None:
        ratio = contrast_ratio(accent, GROUND_BACKGROUND[ground])
        if ratio < MIN_ACCENT_CONTRAST:
            raise AppearanceError(
                f"accent {accent} stands {ratio:.2f}:1 off the {ground} ground; it needs {MIN_ACCENT_CONTRAST:g}:1"
            )
    return cleaned
