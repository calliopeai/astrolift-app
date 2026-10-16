"""Test tiers (#2413).

``slow`` and ``rarely`` are ordinary markers that CI selects with ``-m``:
pull requests skip both, pushes to main skip ``rarely``, and the nightly
workflow runs both. ``manual`` is stricter. A manual test runs only when the
``-m`` expression names the tier, so a CI expression that forgets
``not manual`` still cannot start one. Under ``CI`` they never run, and an
expression that asks for them is refused instead of quietly selecting
nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

import pytest

TIERS = ("slow", "rarely", "manual")

_NAMES_MANUAL = re.compile(r"\bmanual\b")
_NOT_MANUAL = re.compile(r"\bnot\s+manual\b")


def names_manual(markexpr: str | None) -> bool:
    """True when the ``-m`` expression mentions the manual tier at all."""
    return bool(markexpr and _NAMES_MANUAL.search(markexpr))


def refuse_manual_in_ci(markexpr: str | None, environ: Mapping[str, str]) -> None:
    """In CI, ``manual`` may appear only as ``not manual``."""
    if environ.get("CI") and names_manual(_NOT_MANUAL.sub("", markexpr or "")):
        raise pytest.UsageError(
            f"-m {markexpr!r} selects manual tests, which never run in CI (#2413). "
            "Run them locally with -m manual."
        )


def split_manual(markexpr: str | None, environ: Mapping[str, str], items: Iterable) -> tuple[list, list]:
    """Return (kept, deselected). Manual tests stay only when ``-m`` names
    the tier outside CI; pytest's own ``-m`` filter then decides which run."""
    items = list(items)
    if names_manual(markexpr) and not environ.get("CI"):
        return items, []
    kept, manual = [], []
    for item in items:
        (manual if item.get_closest_marker("manual") else kept).append(item)
    return kept, manual
