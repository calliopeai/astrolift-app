"""An empty RED panel says which of the two empty states it is (#1708).

Traffic / errors / latency can only come from an edge-metrics mapping on
the cluster's ingress variant (#1224), the app's own ``metrics.enabled``
instrumentation (#1226), or -- golden signals only -- the ALB/CloudWatch
fallback on AWS. With none of the three the PromQL selects a series that
cannot exist, and the panel used to render exactly as it does for a
healthy app with no traffic: "no data for this window". Saturation keeps
populating from cAdvisor, so the group read as half-broken rather than
unconfigured.

These pin the per-signal ``reason``: NOT_CONFIGURED when no source
exists, NO_DATA_YET when one does and the window is simply empty.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability import prom_client
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_observability.schema.types import GoldenSignalKind
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.schema.enums import ObservabilityPanelReason
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_RED = {
    GoldenSignalKind.TRAFFIC,
    GoldenSignalKind.ERRORS,
    GoldenSignalKind.LATENCY_P50,
    GoldenSignalKind.LATENCY_P90,
    GoldenSignalKind.LATENCY_P95,
    GoldenSignalKind.LATENCY_P99,
}
_SATURATION = {GoldenSignalKind.SATURATION_CPU, GoldenSignalKind.SATURATION_MEMORY}

_SEQ = {"n": 0}


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


def _scaffold(
    *,
    ingress_class: str = "traefik",
    metrics_enabled: bool = False,
    plugin_slug: str = "k8s",
) -> tuple[Organization, RegisteredApp]:
    """Org + cluster + app + env.

    Defaults are the shape the issue describes: an ingress variant with
    no edge-metrics mapping (``traefik`` is a recorded parity gap) and an
    app whose manifest declares no metrics port.
    """
    _SEQ["n"] += 1
    n = _SEQ["n"]
    org = Organization.objects.create(name=f"Acme Red {n}", slug=f"acme-red-{n}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-red-{n}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-red-{n}")

    # ``ProviderPlugin.save`` bumps ``version`` on every write; bypass it.
    plugin_obj = ProviderPlugin(
        name="Plugin",
        slug=f"{plugin_slug}-red-{n}" if plugin_slug != "aws" else "aws",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin_obj], ignore_conflicts=True)
    plugin = ProviderPlugin.objects.get(slug=plugin_obj.slug)

    cluster = TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug=f"prod-red-{n}",
        provider_plugin=plugin,
        provider_config={"prometheus_endpoint": "http://prom:9090"},
        endpoint="https://k8s.invalid",
        ingress_class=ingress_class,
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello Red",
        slug=f"hello-red-{n}",
        k8s_namespace=f"acme-hello-{n}",
        provisioning_status="ready",
        manifest_normalized={
            "name": "hello",
            "workloads": [
                {"name": "web", "metrics_enabled": metrics_enabled, "metrics_port": 9100},
            ],
        },
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://hello-red.example.com",
    )
    return org, app


def _signals(org, app, *, series=None):
    with patch.object(prom_client, "query_range_series", return_value=series or []):
        with tenant_context(TenantContext(organization_id=org.id)):
            return GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug=app.slug)


def _by_kind(result):
    return {s.name: s for s in result.signals}


# ----------------------------------------------------------------------
# golden signals
# ----------------------------------------------------------------------


def test_no_edge_mapping_and_no_instrumentation_is_not_configured(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org, app = _scaffold()

    signals = _by_kind(_signals(org, app))

    for kind in _RED:
        assert signals[kind].reason == ObservabilityPanelReason.NOT_CONFIGURED, kind


def test_saturation_is_never_reported_unconfigured(permission_resolver):
    """cAdvisor is scraped on every cluster, so an empty saturation
    series really is "nothing in the window" -- mislabelling it would
    send an operator hunting for config that is already there."""
    permission_resolver.grant(Permission.APP_READ)
    org, app = _scaffold()

    signals = _by_kind(_signals(org, app))

    for kind in _SATURATION:
        assert signals[kind].reason == ObservabilityPanelReason.NO_DATA_YET, kind


def test_edge_mapping_makes_an_empty_panel_no_data_yet(permission_resolver):
    """nginx maps to an edge-metrics variant, so the query has a source
    and an empty window is a real "no traffic"."""
    permission_resolver.grant(Permission.APP_READ)
    org, app = _scaffold(ingress_class="nginx")

    signals = _by_kind(_signals(org, app))

    for kind in _RED:
        assert signals[kind].reason == ObservabilityPanelReason.NO_DATA_YET, kind


def test_app_instrumentation_makes_an_empty_panel_no_data_yet(permission_resolver):
    """The manifest declaring ``metrics.enabled`` on any workload is the
    other source; the unmapped ingress variant no longer matters."""
    permission_resolver.grant(Permission.APP_READ)
    org, app = _scaffold(metrics_enabled=True)

    signals = _by_kind(_signals(org, app))

    for kind in _RED:
        assert signals[kind].reason == ObservabilityPanelReason.NO_DATA_YET, kind


def test_signals_carrying_samples_are_ok(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org, app = _scaffold()

    result = _signals(org, app, series=[("", [(1.0, 2.0), (2.0, 3.0)])])

    assert result.reason == ObservabilityPanelReason.OK
    assert all(s.reason == ObservabilityPanelReason.OK for s in result.signals)


def test_reachable_cloudwatch_with_no_traffic_is_no_data_yet(permission_resolver, monkeypatch):
    """On AWS the ALB is a third source. Reaching it and being told
    there was no traffic is NO_DATA_YET; only an unreachable one leaves
    the app with no source at all."""
    from core import cluster_management

    permission_resolver.grant(Permission.APP_READ)
    org, app = _scaffold(plugin_slug="aws")
    monkeypatch.setattr(
        cluster_management,
        "cluster_alb_http_metrics_dispatch",
        lambda **kw: {"rps": [], "error_rate": [], "latency_p50": []},
    )

    signals = _by_kind(_signals(org, app))

    for kind in _RED:
        assert signals[kind].reason == ObservabilityPanelReason.NO_DATA_YET, kind


def test_cloudwatch_is_not_consulted_when_prometheus_has_http_data(permission_resolver, monkeypatch):
    """The fallback exists for uninstrumented apps. It was reached on
    every request because the "are the HTTP signals empty?" test
    compared enum values against enum keys and so never matched -- which
    let CloudWatch overwrite live Prometheus series."""
    from core import cluster_management

    permission_resolver.grant(Permission.APP_READ)
    org, app = _scaffold(ingress_class="nginx", plugin_slug="aws")
    calls: list[dict] = []

    def _spy(**kw):
        calls.append(kw)
        return {"rps": [(1.0, 99.0)]}

    monkeypatch.setattr(cluster_management, "cluster_alb_http_metrics_dispatch", _spy)

    result = _signals(org, app, series=[("", [(1.0, 2.0)])])

    assert calls == []
    traffic = _by_kind(result)[GoldenSignalKind.TRAFFIC]
    assert [s.value for s in traffic.samples] == [2.0]


# ----------------------------------------------------------------------
# status-code breakdown
# ----------------------------------------------------------------------


def _breakdown(org, app):
    with patch.object(prom_client, "query_range_series", return_value=[]):
        with tenant_context(TenantContext(organization_id=org.id)):
            return GoldenSignalsQuery().astrolift_app_status_code_breakdown(_info(), app_slug=app.slug)


def test_status_breakdown_without_a_source_is_not_configured(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org, app = _scaffold()

    assert _breakdown(org, app).reason == ObservabilityPanelReason.NOT_CONFIGURED


def test_status_breakdown_with_a_source_is_no_data_yet(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org, app = _scaffold(ingress_class="nginx")

    assert _breakdown(org, app).reason == ObservabilityPanelReason.NO_DATA_YET
