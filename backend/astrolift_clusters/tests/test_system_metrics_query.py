"""Tests for ``astroliftClusterSystemMetrics`` — the cloud-provider
(CloudWatch ALB) system-metrics resolver powering the platform metrics
dashboard's "System metrics" panel.

Boundaries covered:

* permission denied without ``cluster.register`` (the read-side
  permission shared with the sibling Prometheus resolvers)
* missing / soft-deleted cluster -> available=False (empty-state card,
  not a router-level 404)
* non-AWS provider -> available=False / reason='not_supported' (GCP /
  k8s_native have no cloud-metrics driver wired)
* driver-call failure -> available=False / reason='unreachable'
* happy path -> the three golden-signal series (request rate, error
  rate, p95 latency) surfaced from the dispatch with current = last
  point
* default namespace falls back to ``astrolift-system`` when the cluster
  hosts no apps, and an explicit ``app_namespace`` is passed through
* range / step inputs are clamped before hitting the dispatch
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


@pytest.fixture
def org():
    return Organization.objects.create(
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:6]}",
    )


def _make_plugin(slug: str) -> ProviderPlugin:
    # Skip BaseCoreModel.save (numeric version-increment vs CharField)
    # the same way the status-tab test suite does.
    [row] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=slug,
                slug=slug,
                capabilities_manifest={},
                config_schema={},
            ),
        ],
    )
    return row


def _make_cluster(org: Organization, plugin: ProviderPlugin) -> TenantCluster:
    return TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


@pytest.fixture
def aws_cluster(org):
    return _make_cluster(org, _make_plugin("aws"))


@pytest.fixture
def gcp_cluster(org):
    return _make_cluster(org, _make_plugin(f"gcp-{uuid.uuid4().hex[:6]}"))


_FAKE_METRICS = {
    "rps": [(1000.0, 2.0), (1060.0, 3.0)],
    "error_rate": [(1000.0, 0.0), (1060.0, 0.01)],
    "latency_p50": [(1000.0, 0.02), (1060.0, 0.03)],
    "latency_p95": [(1000.0, 0.10), (1060.0, 0.12)],
    "latency_p99": [(1000.0, 0.20), (1060.0, 0.25)],
}


def _patch_dispatch(monkeypatch, *, captured: dict | None = None, raises: bool = False):
    """Monkeypatch cluster_alb_http_metrics_dispatch at the module of
    truth. The resolver imports it lazily from ``core.cluster_management``
    at call time, so patching the module attribute takes effect."""
    from core import cluster_management

    def _fake(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        if raises:
            raise cluster_management.ClusterManagementError("unreachable")
        return dict(_FAKE_METRICS)

    monkeypatch.setattr(cluster_management, "cluster_alb_http_metrics_dispatch", _fake)


# ─── Resolver-level tests ────────────────────────────────────────────


def test_system_metrics_denied_without_cluster_register(aws_cluster, org, permission_resolver):
    """The resolver shares the read-side permission gate with the other
    cluster Status/metrics resolvers. Callers without CLUSTER_REGISTER
    must be denied before any cloud I/O fires."""
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_cluster_system_metrics(
                _info(),
                cluster_id=GUID(str(aws_cluster.guid)),
            )


def test_system_metrics_missing_cluster_returns_unavailable(org, permission_resolver):
    """A non-existent cluster guid returns available=False (no reason)
    without raising — mirrors the Prometheus resolver's empty-state
    contract."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_system_metrics(
            _info(),
            cluster_id=GUID(str(uuid.uuid4())),
        )
    assert result.available is False
    assert result.reason is None
    assert result.series == []


def test_system_metrics_soft_deleted_cluster_returns_unavailable(aws_cluster, org, permission_resolver):
    """Soft-deleted clusters are excluded — a deleted row has nothing to
    report."""
    aws_cluster.soft_delete()
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_system_metrics(
            _info(),
            cluster_id=GUID(str(aws_cluster.guid)),
        )
    assert result.available is False


def test_system_metrics_non_aws_returns_not_supported(gcp_cluster, org, permission_resolver, monkeypatch):
    """Non-AWS providers have no cloud-metrics driver wired — the resolver
    short-circuits to reason='not_supported' WITHOUT calling the dispatch,
    so the UI shows a clear 'not available for this provider' state."""
    captured: dict = {}
    _patch_dispatch(monkeypatch, captured=captured)

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_system_metrics(
            _info(),
            cluster_id=GUID(str(gcp_cluster.guid)),
        )
    assert result.available is False
    assert result.reason == "not_supported"
    assert result.source == ""
    assert result.series == []
    # The dispatch must never fire for an unsupported provider.
    assert captured == {}


def test_system_metrics_happy_path(aws_cluster, org, permission_resolver, monkeypatch):
    """AWS cluster + a dispatch that returns data -> available=True,
    source='cloudwatch', and exactly the three golden-signal series with
    current = the last point of each."""
    _patch_dispatch(monkeypatch)

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_system_metrics(
            _info(),
            cluster_id=GUID(str(aws_cluster.guid)),
        )

    assert result.available is True
    assert result.reason is None
    assert result.source == "cloudwatch"
    assert [s.metric for s in result.series] == ["request_rate", "error_rate", "latency_p95"]
    by_metric = {s.metric: s for s in result.series}
    assert by_metric["request_rate"].unit == "rps"
    assert by_metric["request_rate"].current == pytest.approx(3.0)
    assert len(by_metric["request_rate"].points) == 2
    assert by_metric["error_rate"].unit == "ratio"
    assert by_metric["error_rate"].current == pytest.approx(0.01)
    assert by_metric["latency_p95"].unit == "seconds"
    assert by_metric["latency_p95"].current == pytest.approx(0.12)


def test_system_metrics_default_namespace_falls_back_to_system(aws_cluster, org, permission_resolver, monkeypatch):
    """With no apps bound to the cluster, the default namespace resolves
    to 'astrolift-system' and is echoed back so the UI can label the
    panel scope."""
    captured: dict = {}
    _patch_dispatch(monkeypatch, captured=captured)

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_system_metrics(
            _info(),
            cluster_id=GUID(str(aws_cluster.guid)),
        )
    assert result.app_namespace == "astrolift-system"
    assert captured["app_namespace"] == "astrolift-system"


def test_system_metrics_explicit_namespace_passthrough(aws_cluster, org, permission_resolver, monkeypatch):
    """An explicit app_namespace is passed to the dispatch verbatim and
    echoed back."""
    captured: dict = {}
    _patch_dispatch(monkeypatch, captured=captured)

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_system_metrics(
            _info(),
            cluster_id=GUID(str(aws_cluster.guid)),
            app_namespace="acme-web",
        )
    assert result.app_namespace == "acme-web"
    assert captured["app_namespace"] == "acme-web"


def test_system_metrics_clamps_range_and_step(aws_cluster, org, permission_resolver, monkeypatch):
    """Out-of-bounds range/step are clamped (range floor 300s, step floor
    60s) before hitting the dispatch, and the clamped values are echoed."""
    captured: dict = {}
    _patch_dispatch(monkeypatch, captured=captured)

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_system_metrics(
            _info(),
            cluster_id=GUID(str(aws_cluster.guid)),
            range_seconds=100,  # below the 300s floor
            step_seconds=5,  # below the 60s floor
        )
    assert result.range_seconds == 300
    assert result.step_seconds == 60
    assert captured["step_seconds"] == 60
    assert captured["end_unix"] - captured["start_unix"] == 300


def test_system_metrics_dispatch_failure_returns_unreachable(aws_cluster, org, permission_resolver, monkeypatch):
    """A ClusterManagementError from the dispatch (no creds, throttled,
    plugin missing) surfaces as available=False / reason='unreachable'."""
    _patch_dispatch(monkeypatch, raises=True)

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_system_metrics(
            _info(),
            cluster_id=GUID(str(aws_cluster.guid)),
        )
    assert result.available is False
    assert result.reason == "unreachable"
    assert result.series == []
