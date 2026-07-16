"""Tests for the pipeline dispatch router (#82).

Tests the three routing patterns:
  1. "astrolift/default" → any active managed cluster for the org
  2. "cluster:<name>" → find by name or slug
  3. Label matching → os, arch, custom node_labels

The ``_cluster_matches`` and ``route`` functions are tested directly
(pure function unit tests as required by #82 acceptance criteria)
alongside DB-backed integration tests for the queryset filtering.
"""

from __future__ import annotations

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization
from astrolift_pipelines.dispatch_router import (
    _cluster_matches,
    _is_runner_only,
    _normalise_labels,
    route,
    select_cluster,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plugin(slug: str = "local"):
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=slug,
                slug=slug,
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


def _org(slug: str) -> Organization:
    return Organization.objects.create(name=slug, slug=slug)


def _managed_cluster(
    org: Organization,
    plugin: ProviderPlugin,
    *,
    slug: str,
    node_os: str = "linux",
    node_arch: str = "amd64",
    node_labels: list | None = None,
    is_active: bool = True,
) -> TenantCluster:
    return TenantCluster.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED,
        node_os=node_os,
        node_arch=node_arch,
        node_labels=node_labels or [],
        is_active=is_active,
    )


# ---------------------------------------------------------------------------
# Pure-function unit tests (no DB)
# ---------------------------------------------------------------------------


def test_normalise_labels_string():
    result = _normalise_labels("linux")
    assert result == frozenset({"linux"})


def test_normalise_labels_list():
    result = _normalise_labels(["Linux", " arm64 "])
    assert result == frozenset({"linux", "arm64"})


def test_is_runner_only_self_hosted():
    assert _is_runner_only(frozenset({"self-hosted", "linux"})) is True


def test_is_runner_only_macos():
    assert _is_runner_only(frozenset({"macos"})) is True


def test_is_runner_only_false_for_linux():
    assert _is_runner_only(frozenset({"linux", "amd64"})) is False


# ---------------------------------------------------------------------------
# _cluster_matches pure tests (using a fake cluster-like object)
# ---------------------------------------------------------------------------


class _FakeCluster:
    def __init__(self, node_os="linux", node_arch="amd64", node_labels=None):
        self.node_os = node_os
        self.node_arch = node_arch
        self.node_labels = node_labels or []


def test_cluster_matches_os():
    cluster = _FakeCluster(node_os="linux")
    assert _cluster_matches(cluster, frozenset({"linux"})) is True  # type: ignore[arg-type]
    assert _cluster_matches(cluster, frozenset({"windows"})) is False  # type: ignore[arg-type]


def test_cluster_matches_arch():
    cluster = _FakeCluster(node_arch="arm64")
    assert _cluster_matches(cluster, frozenset({"arm64"})) is True  # type: ignore[arg-type]
    assert _cluster_matches(cluster, frozenset({"amd64"})) is False  # type: ignore[arg-type]


def test_cluster_matches_custom_labels():
    cluster = _FakeCluster(node_labels=["gpu", "high-memory"])
    assert _cluster_matches(cluster, frozenset({"gpu"})) is True  # type: ignore[arg-type]
    assert _cluster_matches(cluster, frozenset({"gpu", "high-memory"})) is True  # type: ignore[arg-type]
    assert _cluster_matches(cluster, frozenset({"gpu", "bare-metal"})) is False  # type: ignore[arg-type]


def test_cluster_matches_combined():
    cluster = _FakeCluster(node_os="linux", node_arch="arm64", node_labels=["gpu"])
    labels = frozenset({"linux", "arm64", "gpu"})
    assert _cluster_matches(cluster, labels) is True  # type: ignore[arg-type]


def test_cluster_matches_case_insensitive_labels():
    cluster = _FakeCluster(node_labels=["GPU"])
    assert _cluster_matches(cluster, frozenset({"gpu"})) is True  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# DB-backed routing tests
# ---------------------------------------------------------------------------


def test_default_routing_picks_any_managed_cluster():
    org = _org("default-route-org")
    plugin = _plugin("default-route")
    _managed_cluster(org, plugin, slug="c1")
    result = route("astrolift/default", org)
    assert result.cluster is not None
    assert result.cluster.slug == "c1"
    assert result.runner_only is False


def test_default_routing_no_clusters_returns_none():
    org = _org("empty-org")
    result = route("astrolift/default", org)
    assert result.cluster is None
    assert result.runner_only is False


def test_cluster_name_routing_by_slug():
    org = _org("named-route-org")
    plugin = _plugin("named-route")
    _managed_cluster(org, plugin, slug="prod-cluster")
    result = route("cluster:prod-cluster", org)
    assert result.cluster is not None
    assert result.cluster.slug == "prod-cluster"


def test_cluster_name_routing_by_name_case_insensitive():
    org = _org("named-ci-org")
    plugin = _plugin("named-ci")
    _managed_cluster(org, plugin, slug="ci-cluster")
    # slug matches first, but name lookup is case-insensitive
    result = route("cluster:CI-Cluster", org)
    # slug lookup: "ci-cluster" != "CI-Cluster" → falls through to name lookup
    # name is "ci-cluster" (same as slug when created) — iexact match
    assert result.cluster is not None


def test_cluster_name_routing_not_found():
    org = _org("missing-cluster-org")
    result = route("cluster:nonexistent", org)
    assert result.cluster is None
    assert "nonexistent" in result.reason


def test_label_routing_os_match():
    org = _org("os-match-org")
    plugin = _plugin("os-match")
    _managed_cluster(org, plugin, slug="linux-c", node_os="linux")
    result = route(["linux"], org)
    assert result.cluster is not None
    assert result.cluster.slug == "linux-c"


def test_label_routing_os_no_match():
    org = _org("os-nomatch-org")
    plugin = _plugin("os-nomatch")
    _managed_cluster(org, plugin, slug="linux-c2", node_os="linux")
    result = route(["windows"], org)
    assert result.cluster is None


def test_label_routing_arch_match():
    org = _org("arch-match-org")
    plugin = _plugin("arch-match")
    _managed_cluster(org, plugin, slug="arm-c", node_arch="arm64")
    result = route(["arm64"], org)
    assert result.cluster is not None
    assert result.cluster.slug == "arm-c"


def test_label_routing_custom_labels():
    org = _org("custom-label-org")
    plugin = _plugin("custom-label")
    _managed_cluster(org, plugin, slug="gpu-c", node_labels=["gpu", "high-memory"])
    result = route(["gpu"], org)
    assert result.cluster is not None
    assert result.cluster.slug == "gpu-c"


def test_label_routing_custom_labels_no_match():
    org = _org("custom-nomatch-org")
    plugin = _plugin("custom-nomatch")
    _managed_cluster(org, plugin, slug="plain-c", node_labels=["standard"])
    result = route(["gpu"], org)
    assert result.cluster is None


def test_self_hosted_label_returns_runner_only():
    org = _org("runner-only-org")
    plugin = _plugin("runner-only")
    _managed_cluster(org, plugin, slug="k8s-c")  # has a K8s cluster
    result = route(["self-hosted", "linux"], org)
    assert result.runner_only is True
    assert result.cluster is None


def test_macos_label_implies_runner_only():
    org = _org("macos-org")
    plugin = _plugin("macos")
    _managed_cluster(org, plugin, slug="linux-c3")
    result = route(["macos", "arm64"], org)
    assert result.runner_only is True
    assert result.cluster is None


def test_astrolift_label_picks_any_cluster():
    org = _org("astrolift-label-org")
    plugin = _plugin("astrolift-label")
    _managed_cluster(org, plugin, slug="any-c")
    result = route(["astrolift"], org)
    assert result.cluster is not None


def test_excludes_registered_not_managed():
    org = _org("registered-org")
    plugin = _plugin("registered-p")
    TenantCluster.objects.create(
        organization=org,
        name="reg-c",
        slug="reg-c",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        # default lifecycle = REGISTERED
    )
    result = route("astrolift/default", org)
    assert result.cluster is None


def test_excludes_inactive_clusters():
    org = _org("inactive-org")
    plugin = _plugin("inactive-p")
    _managed_cluster(org, plugin, slug="inactive-c", is_active=False)
    result = route("astrolift/default", org)
    assert result.cluster is None


def test_excludes_other_org_clusters():
    org_a = _org("router-org-a")
    org_b = _org("router-org-b")
    plugin = _plugin("crossorg-p")
    _managed_cluster(org_b, plugin, slug="b-cluster")
    result = route("astrolift/default", org_a)
    assert result.cluster is None


def test_select_cluster_convenience_wrapper():
    org = _org("convenience-org")
    plugin = _plugin("convenience-p")
    _managed_cluster(org, plugin, slug="conv-c")
    cluster = select_cluster("astrolift/default", org)
    assert cluster is not None
    assert cluster.slug == "conv-c"


def test_empty_runs_on_returns_none():
    org = _org("empty-runs-on-org")
    result = route("", org)
    assert result.cluster is None
    assert "empty" in result.reason.lower()
