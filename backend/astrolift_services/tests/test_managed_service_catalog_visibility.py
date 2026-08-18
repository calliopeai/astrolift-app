"""What the managed-service catalogue offers, and what it leaves out.

The catalogue is what an operator picks from. A row they cannot act on is not
neutral: it crowds out the ones they can, and "planned" reads to most people as
"available soon" rather than "no driver exists".
"""

from __future__ import annotations

import pytest

from astrolift_services.managed_service_catalog import (
    CatalogResolutionError,
    list_catalog,
    resolve_variant,
)
from providers._sdk.availability import MATRIX


def _planned(plugin_slug: str) -> list[tuple[str, str]]:
    return [
        (e.kind, e.variant)
        for e in MATRIX.managed_services
        if e.plugin_id == plugin_slug and e.status == "planned"
    ]


PLUGINS_WITH_PLANNED = [slug for slug in ("gcp", "azure", "k8s_native") if _planned(slug)]


@pytest.mark.parametrize("plugin_slug", PLUGINS_WITH_PLANNED)
def test_planned_variants_are_not_offered(plugin_slug):
    """Roadmap metadata. No driver exists and none is being installed."""
    offered = {(row.kind, row.variant) for row in list_catalog(plugin_slug)}

    for key in _planned(plugin_slug):
        assert key not in offered, f"{plugin_slug} {key[0]}:{key[1]} is planned but offered"


@pytest.mark.parametrize("plugin_slug", PLUGINS_WITH_PLANNED)
def test_planned_variants_are_still_visible_when_asked_for(plugin_slug):
    """Hidden from the catalogue, not deleted from it. Callers that need to
    distinguish "not offered here" from "no such variant" ask for everything."""
    everything = {(row.kind, row.variant) for row in list_catalog(plugin_slug, include_unprovisionable=True)}

    for key in _planned(plugin_slug):
        assert key in everything


def test_an_unavailable_row_always_says_why():
    """Driverless rows stay visible on purpose, so that a control plane whose
    plugin registry failed to load shows a catalogue full of "driver is not
    installed" rather than an empty one. That only helps if each row carries
    the reason."""
    for plugin_slug in ("aws", "gcp", "azure", "k8s_native"):
        for row in list_catalog(plugin_slug):
            if not row.available:
                assert row.unavailable_reason, f"{plugin_slug} {row.kind}:{row.variant}"


def test_a_registry_that_loaded_nothing_does_not_render_an_empty_catalogue():
    """The failure this filter must not create. Hiding driverless rows would
    turn a broken plugin registry into a catalogue with nothing in it, and an
    operator cannot search for what is not shown."""
    for plugin_slug in ("aws", "gcp", "azure", "k8s_native"):
        matrix_entries = [e for e in MATRIX.managed_services if e.plugin_id == plugin_slug]
        if not matrix_entries:
            continue
        planned = len([e for e in matrix_entries if e.status == "planned"])
        assert len(list_catalog(plugin_slug)) == len(matrix_entries) - planned, plugin_slug


def test_deprecated_variants_stay_visible():
    """An operator may already be running one, and the catalogue describes
    their estate as well as their choices."""
    deprecated = [
        (e.plugin_id, e.kind, e.variant) for e in MATRIX.managed_services if e.status == "deprecated"
    ]
    if not deprecated:
        pytest.skip("no deprecated entries in the matrix")

    for plugin_slug, kind, variant in deprecated:
        everything = {(r.kind, r.variant) for r in list_catalog(plugin_slug, include_unprovisionable=True)}
        assert (kind, variant) in everything


def test_asking_for_a_planned_variant_names_the_reason():
    """Filtering must not turn "not offered here" into something that reads
    like a typo. The operator asked for a real variant."""
    candidates = [(slug, *key) for slug in PLUGINS_WITH_PLANNED for key in _planned(slug)]
    if not candidates:
        pytest.skip("no planned entries in the matrix")
    plugin_slug, kind, variant = candidates[0]

    with pytest.raises(CatalogResolutionError) as excinfo:
        resolve_variant(plugin_slug=plugin_slug, kind=kind, requested_variant=variant)

    message = str(excinfo.value)
    assert "unavailable" in message
    assert "does not support" not in message


def test_hiding_does_not_change_which_variant_is_default():
    """Defaults are computed from what is available, so filtering the list must
    not shift the default for a kind that had a planned sibling."""
    for plugin_slug in PLUGINS_WITH_PLANNED:
        offered = {(r.kind, r.variant) for r in list_catalog(plugin_slug) if r.is_default_for_kind}
        everything = {
            (r.kind, r.variant)
            for r in list_catalog(plugin_slug, include_unprovisionable=True)
            if r.is_default_for_kind
        }
        assert offered == everything, plugin_slug
