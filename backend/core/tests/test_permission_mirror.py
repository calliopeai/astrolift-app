"""Drift guard for the frontend permission mirror (#1490).

The UI narrows permission checks against a slug union generated from
``core.permissions.Permission``. Hand-maintained, it fell 29 slugs behind
without anything failing: a grant the backend enforced simply could not be
written in the UI. These tests are what makes the mirror a contract rather
than a copy somebody remembers to update.
"""

from __future__ import annotations

import re

import pytest

from core.management.commands.make_perms import OUTPUT, render
from core.permissions import all_permissions

# ``frontend/`` is not mounted into the backend container, so ``make test``
# cannot see the artifact. CI runs the suite against a full checkout, which
# is where the guard has to hold.
FRONTEND = OUTPUT.parents[2]

pytestmark = pytest.mark.skipif(
    not FRONTEND.is_dir(),
    reason="frontend/ is not mounted into the backend container",
)

SLUG_LINE = re.compile(r'^  "([^"]+)",$', re.MULTILINE)


def _committed_slugs() -> list[str]:
    return SLUG_LINE.findall(OUTPUT.read_text(encoding="utf-8"))


def test_committed_mirror_lists_every_backend_permission() -> None:
    committed = set(_committed_slugs())
    catalog = {perm.value for perm in all_permissions()}
    assert committed == catalog, (
        "frontend/lib/permissions/permissions.generated.ts has drifted from "
        f"core.permissions.Permission; run `make perms`. Missing from the UI: "
        f"{sorted(catalog - committed)}. Not in the catalog: {sorted(committed - catalog)}."
    )


def test_committed_mirror_is_byte_identical_to_the_generator() -> None:
    """Catches hand-edits to the parts a slug-set comparison cannot see —
    the do-not-edit banner, the exported names, the const assertion."""

    assert OUTPUT.read_text(encoding="utf-8") == render(), (
        "frontend/lib/permissions/permissions.generated.ts is stale or was "
        "edited by hand; run `make perms`."
    )


def test_slugs_are_emitted_in_sorted_order() -> None:
    """Adding one permission should be a one-line diff, not a reshuffle."""

    slugs = _committed_slugs()
    assert slugs == sorted(slugs)
