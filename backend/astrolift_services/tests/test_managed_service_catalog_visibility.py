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
from providers._sdk.coverage import OPT_IN_TIER


def _planned(plugin_slug: str) -> list[tuple[str, str]]:
    return [
        (e.kind, e.variant)
        for e in MATRIX.managed_services
        if e.plugin_id == plugin_slug and e.status == "planned"
    ]


def _opt_in(plugin_slug: str) -> list[tuple[str, str]]:
    return [
        (e.kind, e.variant)
        for e in MATRIX.managed_services
        if e.plugin_id == plugin_slug and e.status != "planned" and e.kind in OPT_IN_TIER
    ]


def _everything(plugin_slug: str) -> set[tuple[str, str]]:
    """Every row the catalogue knows, including the ones it does not offer by default.

    Both filters, because they are separate axes: a planned row has no driver,
    an opt-in row has one and is fully provisionable but sits outside the
    surface Astrolift guarantees across clouds. Asking for one of the two and
    calling the result "everything" is what broke these tests when tiering
    landed, so the idiom lives in one place and a third filter has one call
    site to update rather than four.
    """
    rows = list_catalog(plugin_slug, include_unprovisionable=True, include_extended=True)
    return {(row.kind, row.variant) for row in rows}


PLUGINS_WITH_PLANNED = [slug for slug in ("gcp", "azure", "k8s_native") if _planned(slug)]
PLUGINS_WITH_OPT_IN = [slug for slug in ("aws", "gcp", "azure", "k8s_native") if _opt_in(slug)]


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
    everything = _everything(plugin_slug)

    for key in _planned(plugin_slug):
        assert key in everything


def test_an_unavailable_row_always_says_why():
    """Driverless rows stay visible on purpose, so that a control plane whose
    plugin registry failed to load shows a catalogue full of "driver is not
    installed" rather than an empty one. That only helps if each row carries
    the reason."""
    for plugin_slug in ("aws", "gcp", "azure", "k8s_native"):
        for row in list_catalog(plugin_slug, include_extended=True):
            if not row.available:
                assert row.unavailable_reason, f"{plugin_slug} {row.kind}:{row.variant}"


def test_a_registry_that_loaded_nothing_does_not_render_an_empty_catalogue():
    """The failure this filter must not create. Hiding driverless rows would
    turn a broken plugin registry into a catalogue with nothing in it, and an
    operator cannot search for what is not shown.

    A cloud catalogue is a superset of that plugin's own matrix rows rather
    than an exact match, because a cloud-hosted cluster also books the
    in-cluster drivers (#1484). The next test pins what the extras may be.

    Opt-in kinds are excluded here for the same reason planned ones are: the
    default catalogue is not meant to carry them (#1470). That is a narrowing
    of this guard, so the test below holds the other half and asserts they are
    still reachable -- between them nothing real can go missing unnoticed."""
    for plugin_slug in ("aws", "gcp", "azure", "k8s_native"):
        own = {
            (e.kind, e.variant)
            for e in MATRIX.managed_services
            if e.plugin_id == plugin_slug and e.status != "planned" and e.kind not in OPT_IN_TIER
        }
        if not own:
            continue
        offered = {(row.kind, row.variant) for row in list_catalog(plugin_slug)}

        assert own <= offered, f"{plugin_slug} drops {sorted(own - offered)}"


def test_a_planned_row_is_never_reported_as_a_broken_install():
    """The reason a planned variant cannot be provisioned is that nobody has
    written it, not that this control plane is missing a plugin.

    A planned row has no driver by definition, so testing for the driver first
    matched every one of them and sent operators to check an install that was
    fine. Eight of the nine planned rows in the matrix said so, and the planned
    message was reachable only by the single planned variant that ships a stub
    (#1470).
    """
    seen = 0
    for plugin_slug in ("aws", "gcp", "azure", "k8s_native"):
        rows = {
            (r.kind, r.variant): r
            for r in list_catalog(plugin_slug, include_unprovisionable=True, include_extended=True)
        }
        for key in _planned(plugin_slug):
            row = rows[key]
            seen += 1
            assert "not installed" not in row.unavailable_reason, f"{plugin_slug} {key[0]}:{key[1]}"
    assert seen, "no planned rows in the matrix; this test would assert nothing"


def test_a_planned_variant_of_a_cut_kind_does_not_read_as_roadmap():
    """ "Planned" reads as "available soon". For a kind that has been cut it
    means the opposite: nobody intends to build it. The row stays for the
    record and has to say which of the two it is (#1470)."""
    checked = 0
    for plugin_slug in ("aws", "gcp", "azure", "k8s_native"):
        rows = {
            (r.kind, r.variant): r
            for r in list_catalog(plugin_slug, include_unprovisionable=True, include_extended=True)
        }
        for kind, variant in _planned(plugin_slug):
            if kind not in OPT_IN_TIER:
                continue
            checked += 1
            assert (
                "not planned" in rows[(kind, variant)].unavailable_reason
            ), f"{plugin_slug} {kind}:{variant}"
    assert checked, "no planned rows on cut kinds; this test would assert nothing"


@pytest.mark.parametrize("plugin_slug", PLUGINS_WITH_OPT_IN)
def test_opt_in_kinds_are_hidden_by_default_but_not_lost(plugin_slug):
    """Cut from the default catalogue, still in the tree and still bookable.

    The drivers work and someone may already depend on one, so the cut is a
    statement about the guaranteed surface rather than a removal. Without this
    the completeness guard above could be narrowed until it asserted nothing.
    """
    offered = {(row.kind, row.variant) for row in list_catalog(plugin_slug)}
    everything = _everything(plugin_slug)

    for key in _opt_in(plugin_slug):
        assert key not in offered, f"{plugin_slug} {key[0]}:{key[1]} is opt-in but offered by default"
        assert key in everything, f"{plugin_slug} {key[0]}:{key[1]} is unreachable"


@pytest.mark.parametrize("plugin_slug", ["aws", "gcp", "azure"])
def test_the_only_borrowed_rows_are_in_cluster_ones(plugin_slug):
    """The catalogue widened by exactly one plugin. A row from a *different*
    cloud would be an offer the cluster has no credentials to honour, and the
    resolver would refuse it after the operator had already picked it."""
    own = {(e.kind, e.variant) for e in MATRIX.managed_services if e.plugin_id == plugin_slug}
    in_cluster = {(e.kind, e.variant) for e in MATRIX.managed_services if e.plugin_id == "k8s_native"}
    offered = {(row.kind, row.variant) for row in list_catalog(plugin_slug)}

    assert offered - own <= in_cluster, f"{plugin_slug} offers {sorted(offered - own - in_cluster)}"


def test_deprecated_variants_stay_visible():
    """An operator may already be running one, and the catalogue describes
    their estate as well as their choices."""
    deprecated = [
        (e.plugin_id, e.kind, e.variant) for e in MATRIX.managed_services if e.status == "deprecated"
    ]
    if not deprecated:
        pytest.skip("no deprecated entries in the matrix")

    for plugin_slug, kind, variant in deprecated:
        everything = _everything(plugin_slug)
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
