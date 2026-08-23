"""The portability rule, checked against the driver registry that has to honour it (#1484).

``_sdk.coverage.is_portable`` says a kind is reachable on every cloud once it
has an in-cluster variant, because Astrolift runs the cluster. Every guard
around it -- the gap ledger, the coverage doc, the certification collection --
consumed that answer and proved only that it was applied *consistently*. None
of them proved it was *true*, and it was not: driver lookup was scoped to the
cluster's own plugin, no cloud plugin registers a ``k8s_native`` driver, and so
``cache``, ``observability``, ``search`` and ``workflow_engine`` were reported
portable while no EKS-, GKE- or AKS-hosted app could book the variant that made
them so.

These tests are the missing half. They build the registry the control plane
builds, from the real plugin manifests through the real loader, and resolve a
real driver class for every cell the rule claims. A guard over a model of the
system is only as good as the model, so this one goes to the system.
"""

from __future__ import annotations

import pytest
from astrolift_clusters.plugin_loader import _adapt
from astrolift_drivers.managed_resolution import (
    IN_CLUSTER_PLUGIN,
    managed_role,
    resolution_order,
    resolve_managed_driver,
)
from astrolift_drivers.registry import DriverNotFound, PluginManifest, PluginRegistry

from _sdk.coverage import CLOUDS, EXECUTABLE_STATUSES, IN_CLUSTER, coverage

PLUGIN_MODULES = {
    "aws": "aws.plugin",
    "gcp": "gcp.plugin",
    "azure": "azure.plugin",
    "k8s_native": "k8s_native.plugin",
}


def _plugin(plugin_id: str):
    module = __import__(PLUGIN_MODULES[plugin_id], fromlist=["PLUGIN"])
    return module.PLUGIN


@pytest.fixture(scope="module")
def registry() -> PluginRegistry:
    """The registry the control plane runs on, minus the entry-point discovery.

    Built through ``plugin_loader._adapt`` rather than by reading
    ``managed_service_drivers`` directly, so the synthetic ``managed:`` role
    spelling is exercised end to end instead of being reimplemented here --
    where a typo would make the test agree with itself.
    """
    reg = PluginRegistry()
    for plugin_id in PLUGIN_MODULES:
        reg.register(_adapt(plugin_id, _plugin(plugin_id)))
    return reg


def _in_cluster_only_rows():
    """Kinds the rule calls portable *only* because of an in-cluster variant."""
    return [row for row in coverage() if row.is_portable and not row.is_cloud_portable]


def _managed_pairs(plugin_id: str) -> set[tuple[str, str]]:
    return set(_plugin(plugin_id).managed_service_drivers)


# ---- the rule, resolved against the runtime ----------------------------------


def test_the_in_cluster_portability_rule_names_the_kinds_we_think_it_does():
    """Pins the population the next test walks.

    Without this an in-cluster variant could quietly disappear from the matrix
    and the test below would pass over an empty list, which is the same shape
    of vacuous green that let #1484 through in the first place.
    """
    assert {row.kind for row in _in_cluster_only_rows()} == {
        "api_gateway",
        "cache",
        "observability",
        "search",
        "workflow_engine",
    }


def test_every_in_cluster_only_kind_resolves_a_driver_from_every_cloud(registry):
    """The test that would have caught #1484.

    For each kind the rule calls portable on the strength of its in-cluster
    variant, resolve that variant from a cluster running each public cloud's
    plugin. Before the fallback every one of these raised ``DriverNotFound``
    while the coverage doc, the gap ledger and the certification grid all
    reported the kind as covered.
    """
    unreachable = []
    for row in _in_cluster_only_rows():
        for variant in row.cell(IN_CLUSTER).executable:
            for cloud in row.missing_clouds:
                try:
                    resolve_managed_driver(
                        cluster_plugin_slug=cloud,
                        kind=row.kind,
                        variant=variant,
                        registry=registry,
                    )
                except DriverNotFound as exc:
                    unreachable.append(f"{row.kind}/{variant} on {cloud}: {exc}")

    assert not unreachable, (
        "is_portable claims these kinds are reachable on every cloud, but no driver resolves:\n"
        + "\n".join(unreachable)
    )


def test_a_resolved_in_cluster_driver_reports_the_plugin_that_owns_it(registry):
    """The resolved plugin id is not cosmetic: ``managed_config_for`` dispatches
    on it, and a Memcached driver handed an Azure config dies at construction.
    """
    resolved = resolve_managed_driver(
        cluster_plugin_slug="azure",
        kind="cache",
        variant="memcached",
        registry=registry,
    )

    assert resolved.plugin_slug == IN_CLUSTER_PLUGIN
    assert resolved.is_in_cluster
    assert resolved.driver_cls is _plugin("k8s_native").managed_service_drivers[("cache", "memcached")]


# ---- the fallback widens in-cluster reach; it does not merge the plugins -----


def test_a_cloud_managed_variant_still_beats_the_in_cluster_fallback(registry):
    """``postgres`` exists on both sides. An RDS cluster must still get RDS --
    the fallback is a last resort, not a preference.
    """
    resolved = resolve_managed_driver(
        cluster_plugin_slug="aws",
        kind="postgres",
        variant="rds",
        registry=registry,
    )

    assert resolved.plugin_slug == "aws"
    assert not resolved.is_in_cluster


@pytest.mark.parametrize("cloud", CLOUDS)
def test_a_k8s_native_cluster_cannot_reach_a_cloud_driver(cloud, registry):
    """The fallback is one-way. A driver that genuinely needs cloud APIs stays
    unreachable from a plugin that cannot provide them, so a vanilla cluster
    does not silently acquire an RDS or a Key Vault it has no credentials for.
    """
    cloud_only = _managed_pairs(cloud) - _managed_pairs(IN_CLUSTER_PLUGIN)
    assert cloud_only, f"{cloud} registers no drivers of its own; the test proves nothing"

    leaked = [
        f"{kind}/{variant}"
        for kind, variant in sorted(cloud_only)
        if _resolves(registry, IN_CLUSTER_PLUGIN, kind, variant)
    ]

    assert not leaked, f"a k8s_native cluster resolved {cloud}-only drivers: {', '.join(leaked)}"


def _resolves(registry: PluginRegistry, plugin_slug: str, kind: str, variant: str) -> bool:
    try:
        resolve_managed_driver(
            cluster_plugin_slug=plugin_slug,
            kind=kind,
            variant=variant,
            registry=registry,
        )
    except DriverNotFound:
        return False
    return True


def test_an_unknown_variant_still_fails_and_names_what_it_tried(registry):
    """Widening reach must not turn a typo into a silent substitution."""
    with pytest.raises(DriverNotFound) as caught:
        resolve_managed_driver(
            cluster_plugin_slug="aws",
            kind="postgres",
            variant="not_a_variant",
            registry=registry,
        )

    message = str(caught.value)
    assert "aws/managed:postgres:not_a_variant" in message
    assert "k8s_native/managed:postgres:not_a_variant" in message


# ---- ambiguity is decided here, not by dict order ----------------------------


def test_the_cluster_plugin_wins_an_exact_collision():
    """The documented tie-break, exercised against a registry that has a
    collision -- the real one has none, so nothing else would prove the order
    is a decision rather than an accident.

    The cloud plugin wins because a live service was provisioned through it: if
    the in-cluster driver took over, deprovision would call the wrong driver
    against a handle it does not own and leave the cloud resource running.
    """

    class CloudCache: ...

    class InClusterCache: ...

    reg = PluginRegistry()
    reg.register(
        PluginManifest(
            plugin_id="azure",
            display_name="Azure",
            version="0",
            drivers={managed_role("cache", "memcached"): CloudCache},
        ),
    )
    reg.register(
        PluginManifest(
            plugin_id=IN_CLUSTER_PLUGIN,
            display_name="Kubernetes",
            version="0",
            drivers={managed_role("cache", "memcached"): InClusterCache},
        ),
    )

    resolved = resolve_managed_driver(cluster_plugin_slug="azure", kind="cache", variant="memcached", registry=reg)

    assert resolved.driver_cls is CloudCache
    assert resolved.plugin_slug == "azure"


def test_an_exact_in_cluster_match_beats_the_cluster_plugins_variantless_default():
    """A request naming ``memcached`` must not be served by a cloud plugin's
    generic ``managed:cache:`` entry, which would go and provision ElastiCache
    under a name the operator asked for something else by.
    """

    class CloudDefaultCache: ...

    class InClusterMemcached: ...

    reg = PluginRegistry()
    reg.register(
        PluginManifest(
            plugin_id="aws",
            display_name="AWS",
            version="0",
            drivers={managed_role("cache"): CloudDefaultCache},
        ),
    )
    reg.register(
        PluginManifest(
            plugin_id=IN_CLUSTER_PLUGIN,
            display_name="Kubernetes",
            version="0",
            drivers={managed_role("cache", "memcached"): InClusterMemcached},
        ),
    )

    resolved = resolve_managed_driver(cluster_plugin_slug="aws", kind="cache", variant="memcached", registry=reg)

    assert resolved.driver_cls is InClusterMemcached
    assert resolution_order("aws", "cache", "memcached") == (
        ("aws", "managed:cache:memcached"),
        ("k8s_native", "managed:cache:memcached"),
        ("aws", "managed:cache:"),
        ("k8s_native", "managed:cache:"),
    )


def test_no_kind_variant_is_claimed_by_both_a_cloud_plugin_and_k8s_native():
    """Fail closed on a *new* ambiguity.

    The tie-break above is safe for the collisions we can foresee, but it is a
    default, and a default applied silently to a case nobody thought about is
    how #1484 happened. If this fails, a cloud plugin and ``k8s_native`` have
    started claiming the same ``(kind, variant)``: decide deliberately which
    should win for that pair, say why, and then update this list.
    """
    in_cluster = _managed_pairs(IN_CLUSTER_PLUGIN)
    collisions = {
        f"{cloud}:{kind}/{variant}" for cloud in CLOUDS for kind, variant in _managed_pairs(cloud) & in_cluster
    }

    assert not collisions, (
        "a cloud plugin and k8s_native both register: "
        + ", ".join(sorted(collisions))
        + ". See astrolift_drivers/managed_resolution.py for the tie-break this now falls under."
    )


# ---- the matrix and the registry describe the same drivers -------------------


def test_every_executable_in_cluster_variant_in_the_matrix_is_actually_registered():
    """The other direction of the same drift.

    ``is_portable`` reads the availability matrix, so an in-cluster row typed
    into the matrix for a driver that was never registered would make a kind
    portable on paper and unbookable everywhere, on vanilla Kubernetes
    included.
    """
    from _sdk.availability import MATRIX

    registered = _managed_pairs(IN_CLUSTER_PLUGIN)
    missing = sorted(
        f"{entry.kind}/{entry.variant}"
        for entry in MATRIX.managed_services
        if entry.plugin_id == IN_CLUSTER and entry.status in EXECUTABLE_STATUSES
        if (entry.kind, entry.variant) not in registered
    )

    assert not missing, (
        "the matrix calls these in-cluster variants executable, but no driver is registered: " + ", ".join(missing)
    )
