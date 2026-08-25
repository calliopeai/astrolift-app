"""An arch-label no-match explains itself (#1604).

`TenantCluster.node_arch` has no writer anywhere in the repo. The column
exists, `dispatch_router` reads it, and nothing has ever set it, so it holds
its `""` default on every install. The matcher treats an unknown
architecture as a mismatch:

    if required_arch and cluster.node_arch not in required_arch:
        return False

So **every arch-labelled job routes nowhere, on every install** -- and the
failure it produces sends the operator to look at their selector or their
cluster fleet, neither of which is the problem.

These do not change what matches. Treating "unknown" as a match would
schedule arm64 work onto amd64 nodes, which is a worse failure than an
honest refusal. What changes is that the refusal says what is actually
wrong.
"""

from __future__ import annotations

import pytest

from astrolift_pipelines.dispatch_router import ARCH_LABELS, _unrecorded_arch_hint

pytestmark = pytest.mark.django_db


class _Cluster:
    """Stands in for TenantCluster across the matcher's three label checks.

    `node_archs` is stored as given, not coerced with `list()`: coercing
    would turn the bare-string case into ['a','m','d','6','4'] inside the
    stub and test the stub rather than `_arch_set`.
    """

    def __init__(self, slug, node_archs=(), node_os="linux", node_labels=None):
        self.slug = slug
        self.node_archs = node_archs
        self.node_os = node_os
        self.node_labels = node_labels or []


def test_the_hint_names_the_real_cause():
    clusters = [_Cluster("prod-a"), _Cluster("prod-b")]

    hint = _unrecorded_arch_hint(clusters, frozenset({"arm64"}))

    assert "no recorded node architecture" in hint
    assert "prod-a" in hint
    assert "capability reconcile" in hint


def test_no_hint_when_the_selector_has_no_arch():
    """A selector that never mentioned an architecture did not fail for this
    reason, and saying so would be noise."""
    clusters = [_Cluster("prod-a")]

    assert _unrecorded_arch_hint(clusters, frozenset()) == ""


def test_no_hint_once_architectures_are_recorded():
    """The hint has to disappear on its own when #1604's producer lands, or
    it becomes a permanent lie."""
    clusters = [_Cluster("prod-a", ["amd64"]), _Cluster("prod-b", ["arm64"])]

    assert _unrecorded_arch_hint(clusters, frozenset({"arm64"})) == ""


def test_it_does_not_list_every_cluster():
    """A fleet-sized list in an error message is unreadable."""
    clusters = [_Cluster(f"c-{i}") for i in range(20)]

    hint = _unrecorded_arch_hint(clusters, frozenset({"amd64"}))

    assert "20 cluster(s)" in hint
    assert hint.count("c-") <= 4


def test_the_matcher_and_the_diagnostic_share_one_definition():
    """They used to disagree by construction: `arch_labels` was a local
    inside the matcher, so the diagnostic could not see it and a second copy
    was the only way to write this."""
    import ast
    import inspect

    from astrolift_pipelines import dispatch_router

    tree = ast.parse(inspect.getsource(dispatch_router._cluster_matches).lstrip())
    literals = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Set) and any(isinstance(e, ast.Constant) and e.value == "arm64" for e in n.elts)
    ]

    assert literals == [], "the matcher re-declares the arch label set instead of using ARCH_LABELS"
    assert "arm64" in ARCH_LABELS


# ---- the list, and the producer behind it (#1604) -----------------------


def test_a_mixed_fleet_matches_either_architecture():
    """The whole reason the column became a list.

    A cluster with amd64 and arm64 node groups is ordinary. The old
    CharField forced it to claim one or neither, so a producer had no honest
    value to write and never wrote one.
    """
    from astrolift_pipelines.dispatch_router import _cluster_matches

    mixed = _Cluster("mixed", ["amd64", "arm64"])

    assert _cluster_matches(mixed, frozenset({"arm64"}))
    assert _cluster_matches(mixed, frozenset({"amd64"}))


def test_a_single_arch_cluster_still_refuses_the_other():
    from astrolift_pipelines.dispatch_router import _cluster_matches

    assert not _cluster_matches(_Cluster("amd-only", ["amd64"]), frozenset({"arm64"}))


def test_an_unprobed_cluster_matches_no_architecture():
    """Unknown is not a match. Scheduling arm64 work onto nodes whose
    architecture nobody recorded is worse than refusing."""
    from astrolift_pipelines.dispatch_router import _cluster_matches

    assert not _cluster_matches(_Cluster("fresh", []), frozenset({"arm64"}))


def test_a_bare_string_is_tolerated():
    """The field was a CharField until #1604. A fixture or an unmigrated
    caller handing over "amd64" must match, not iterate into single letters."""
    from astrolift_pipelines.dispatch_router import _arch_set

    assert _arch_set(_Cluster("legacy", "amd64")) == frozenset({"amd64"})


def test_the_reconcile_records_what_the_probe_found(db):
    """The producer. Before #1604 nothing wrote this column at all."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_workflows.activities import scheduled

    plugin = ProviderPlugin.objects.create(
        name="k8s_native", slug="k8s_native", capabilities_manifest={}, config_schema={}
    )
    cluster = TenantCluster.objects.create(
        slug="probe-me",
        name="probe-me",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )

    import core.cluster_management as cm

    original = cm.probe_cluster_capabilities_dispatch
    cm.probe_cluster_capabilities_dispatch = lambda *, cluster: {"node_architectures": ["arm64", "amd64"]}
    try:
        scheduled._reconcile_cluster_capabilities_sync()
    finally:
        cm.probe_cluster_capabilities_dispatch = original

    cluster.refresh_from_db()
    assert cluster.node_archs == ["amd64", "arm64"]


def test_a_probe_that_found_nothing_does_not_clear_a_known_value(db):
    """A transient RBAC or network failure returns an empty list. Clearing on
    that would stop every arch-labelled job routing until the next successful
    tick, which is a worse outcome than a stale value."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_workflows.activities import scheduled

    plugin = ProviderPlugin.objects.create(
        name="k8s_native", slug="k8s_native", capabilities_manifest={}, config_schema={}
    )
    cluster = TenantCluster.objects.create(
        slug="known",
        name="known",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        node_archs=["amd64"],
    )

    import core.cluster_management as cm

    original = cm.probe_cluster_capabilities_dispatch
    cm.probe_cluster_capabilities_dispatch = lambda *, cluster: {"node_architectures": []}
    try:
        scheduled._reconcile_cluster_capabilities_sync()
    finally:
        cm.probe_cluster_capabilities_dispatch = original

    cluster.refresh_from_db()
    assert cluster.node_archs == ["amd64"]
