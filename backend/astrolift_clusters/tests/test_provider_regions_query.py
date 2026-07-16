"""Tests for the provider-region picker backend surface (#860).

Covers:

* resolver permission gate — denied without ``cluster.register``
* resolver happy path — dispatch dicts map 1:1 onto
  ``AstroliftProviderRegion`` rows, order preserved
* resolver driver-resolution failure (plugin not loaded) → empty list
  (the frontend keeps free-entry on top, so an empty list degrades to
  the old behavior rather than blocking registration)
* dispatch layer — converts driver ``RegionInfo`` dataclasses to plain
  dicts and raises ``ClusterManagementError`` only on driver-build
  failure
* dispatch ``_config_for_plugin_slug`` builds a usable config for each
  known cloud and rejects an unknown slug
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


@pytest.fixture
def org():
    import uuid

    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


# ─── Resolver-level tests ────────────────────────────────────────────


def test_provider_regions_denied_without_cluster_register(org, permission_resolver):
    """The register dialog is ``cluster.register``-gated; the region
    resolver shares that permission. Callers without it are denied."""
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_provider_regions(_info(), provider_plugin_slug="aws")


def test_provider_regions_happy_path_maps_rows(org, permission_resolver, monkeypatch):
    """Dispatch returns plain dicts; the resolver maps them 1:1 onto
    ``ProviderRegionType`` rows, preserving order."""
    from core import cluster_management

    def _ok(*, provider_plugin_slug):
        assert provider_plugin_slug == "aws"
        return [
            {"id": "us-east-1", "label": "US East (N. Virginia)", "continent": "Americas"},
            {"id": "eu-west-1", "label": "Europe (Ireland)", "continent": "Europe"},
        ]

    monkeypatch.setattr(cluster_management, "provider_regions_dispatch", _ok)
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        rows = ClustersQuery().astrolift_provider_regions(_info(), provider_plugin_slug="aws")

    assert [(r.id, r.label, r.continent) for r in rows] == [
        ("us-east-1", "US East (N. Virginia)", "Americas"),
        ("eu-west-1", "Europe (Ireland)", "Europe"),
    ]


def test_provider_regions_driver_failure_returns_empty(org, permission_resolver, monkeypatch):
    """``ClusterManagementError`` from the dispatch (plugin not loaded)
    surfaces as an empty list — the picker degrades to free-entry
    rather than erroring the register dialog."""
    from core import cluster_management

    def _boom(*, provider_plugin_slug):
        raise cluster_management.ClusterManagementError("plugin not loaded")

    monkeypatch.setattr(cluster_management, "provider_regions_dispatch", _boom)
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        rows = ClustersQuery().astrolift_provider_regions(_info(), provider_plugin_slug="bogus")

    assert rows == []


# ─── Dispatch-level tests ────────────────────────────────────────────


def test_dispatch_converts_region_info_to_dicts(monkeypatch):
    """``provider_regions_dispatch`` calls the driver's ``list_regions``
    and converts the ``RegionInfo`` dataclasses to plain dicts the
    resolver layer consumes without strawberry coercion."""
    from _sdk.cluster import RegionInfo

    from core import cluster_management

    class _FakeDriver:
        def list_regions(self):
            return [
                RegionInfo(id="us-west-2", label="US West (Oregon)", continent="Americas"),
                RegionInfo(id="xx-9", label="xx-9", continent=""),
            ]

    monkeypatch.setattr(
        cluster_management,
        "_driver_for_plugin_slug",
        lambda slug: _FakeDriver(),
    )
    out = cluster_management.provider_regions_dispatch(provider_plugin_slug="aws")
    assert out == [
        {"id": "us-west-2", "label": "US West (Oregon)", "continent": "Americas"},
        {"id": "xx-9", "label": "xx-9", "continent": ""},
    ]


def test_dispatch_driver_without_list_regions_returns_empty(monkeypatch):
    """A driver that doesn't implement ``list_regions`` (older plugin)
    yields an empty list rather than raising."""
    from core import cluster_management

    monkeypatch.setattr(
        cluster_management,
        "_driver_for_plugin_slug",
        lambda slug: object(),  # no list_regions attribute
    )
    assert cluster_management.provider_regions_dispatch(provider_plugin_slug="aws") == []


def test_dispatch_wraps_driver_exception(monkeypatch):
    """An exception from the driver's ``list_regions`` is wrapped in
    ``ClusterManagementError`` so the resolver's single except clause
    catches it."""
    from core import cluster_management

    class _BoomDriver:
        def list_regions(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(cluster_management, "_driver_for_plugin_slug", lambda slug: _BoomDriver())
    with pytest.raises(cluster_management.ClusterManagementError):
        cluster_management.provider_regions_dispatch(provider_plugin_slug="aws")


def test_config_for_plugin_slug_builds_per_cloud():
    """``_config_for_plugin_slug`` builds a usable bootstrap config for
    every known cloud — AWS pins us-east-1 + a placeholder cluster
    name; the others get empty-default configs."""
    from core.cluster_management import _config_for_plugin_slug

    aws_cfg = _config_for_plugin_slug("aws")
    assert aws_cfg.region == "us-east-1"
    assert aws_cfg.cluster_name == "_region_probe"

    gcp_cfg = _config_for_plugin_slug("gcp")
    assert gcp_cfg.cluster_name == "_region_probe"

    azure_cfg = _config_for_plugin_slug("azure")
    assert azure_cfg.cluster_name == "_region_probe"

    # k8s_native has no cluster-identifying config — empty defaults.
    k8s_cfg = _config_for_plugin_slug("k8s_native")
    assert k8s_cfg is not None


def test_config_for_plugin_slug_unknown_raises():
    """An unknown slug raises ``ClusterManagementError`` so the resolver
    falls back to an empty list."""
    from core.cluster_management import ClusterManagementError, _config_for_plugin_slug

    with pytest.raises(ClusterManagementError):
        _config_for_plugin_slug("bogus")


# ─── Driver static-list smoke (GCP / Azure / k8s_native) ─────────────


def test_gcp_driver_list_regions_static():
    """The GKE driver returns a curated static region list (no live
    compute.regions.list on the no-cluster register path)."""
    from unittest.mock import MagicMock

    from gcp.cluster_gke import GKEClusterDriver, GKEConfig

    driver = GKEClusterDriver(
        config=GKEConfig(
            project_id="", location="", cluster_name="_region_probe", container_client=MagicMock()
        ),
    )
    regions = driver.list_regions()
    slugs = {r.id for r in regions}
    assert "us-central1" in slugs
    assert "europe-west1" in slugs
    assert all(r.label for r in regions)


def test_azure_driver_list_regions_static():
    """The AKS driver returns a curated static region list."""
    from unittest.mock import MagicMock

    from azure.cluster_aks import AKSClusterDriver, AKSConfig

    driver = AKSClusterDriver(
        config=AKSConfig(
            subscription_id="",
            resource_group="",
            cluster_name="_region_probe",
            container_service_client=MagicMock(),
        ),
    )
    regions = driver.list_regions()
    slugs = {r.id for r in regions}
    assert "eastus" in slugs
    assert "westeurope" in slugs
    assert all(r.label for r in regions)


def test_k8s_native_driver_list_regions_empty():
    """k8s_native has no region concept — inherits the SDK default
    empty list so the UI hides the field."""
    from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig

    driver = K8sNativeClusterDriver(config=K8sNativeConfig())
    assert driver.list_regions() == []
