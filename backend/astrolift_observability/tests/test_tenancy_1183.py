"""Cross-tenant isolation for the app-slug observability resolvers (#1183).

``RegisteredApp.slug`` is unique per-org only, so a resolver that looks an
app up by slug without an ``organization_id`` constraint will happily hand
one org's metrics / logs (PII) to any other org that guesses the slug. These
tests prove the fetch is now org-scoped by:

  * pointing a *foreign* tenant at another org's real, fully-wired app slug
    and asserting it gets the empty / not-configured state, and
  * asserting the expensive backend (Prometheus / the log aggregator) is
    NEVER invoked on the foreign path — the resolver bails at the app
    lookup, so no data is fetched to leak in the first place.

A same-org control in each test proves the happy path still reaches the
backend, so the scoping isn't just breaking the feature outright.
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC
from types import SimpleNamespace

import pytest
from _sdk.log_stream import LogLine, LogPage

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability import prom_client
from astrolift_observability.schema.log_queries import LogHistoryQuery
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_registry.models import RegisteredApp
from core.cluster_log_query import (
    reset_log_query_driver_for_tests,
    set_log_query_driver_for_tests,
)
from core.permissions import Permission
from core.schema.enums import ObservabilityPanelReason
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


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


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _scaffold(suffix: str, *, prometheus_endpoint: str | None = None, log_driver: str | None = None):
    """Mint an independent org + cluster + app + env keyed by ``suffix``."""
    org = Organization.objects.create(name=f"Org {suffix}", slug=f"org-{suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{suffix}")

    plugin_obj = ProviderPlugin(
        name="K8s",
        slug=f"k8s-{suffix}",
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin_obj])
    plugin = ProviderPlugin.objects.get(slug=f"k8s-{suffix}")

    cfg: dict = {}
    if prometheus_endpoint:
        cfg["prometheus_endpoint"] = prometheus_endpoint
    if log_driver:
        cfg["log_driver"] = log_driver
        cfg["log_config"] = {"endpoint": "http://loki:3100"}
    cluster = TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug=f"prod-{suffix}",
        provider_plugin=plugin,
        provider_config=cfg,
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=f"App {suffix}",
        slug=f"app-{suffix}",
        k8s_namespace=f"ns-{suffix}",
        provisioning_status="ready",
        default_tenant_cluster=cluster,
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url=f"https://app-{suffix}.example.com",
    )
    return org, app


class _RecordingLogDriver:
    """Records every ``query_logs`` call. Its presence being invoked at all
    on the foreign path would be a leak — the test asserts zero calls."""

    def __init__(self, page: LogPage):
        self.calls: list[dict] = []
        self.page = page

    def query_logs(self, query, since, until, *, limit, cursor, level, search):
        self.calls.append({"query": query})
        return self.page


# ---------------------------------------------------------------------------
# astroliftAppLogs — the log-aggregator PII path
# ---------------------------------------------------------------------------


def test_app_logs_foreign_org_slug_returns_empty_and_never_hits_aggregator(permission_resolver):
    """Org B asks for Org A's app slug: empty page, and the aggregator
    driver is never queried — no log lines are fetched to leak."""
    org_a, app_a = _scaffold("a", log_driver="loki")
    org_b, _ = _scaffold("b")
    permission_resolver.grant(Permission.APP_READ_LOGS)

    driver = _RecordingLogDriver(
        LogPage(
            items=[
                LogLine(
                    timestamp="1700000000000000000",
                    namespace="ns-a",
                    pod="app-a-0",
                    container="web",
                    message="ORG A SECRET LOG LINE",
                    level=None,
                    labels=None,
                )
            ],
            next_cursor="",
        )
    )
    set_log_query_driver_for_tests(driver)
    try:
        since = dt.datetime.now(UTC) - dt.timedelta(minutes=30)
        until = dt.datetime.now(UTC)
        with _tenant(org_b):
            foreign = LogHistoryQuery().astrolift_app_logs(
                _info(), app_slug=app_a.slug, since=since, until=until
            )
        # Snapshot the foreign-path call count BEFORE the same-org control
        # runs (the control legitimately adds a call).
        calls_after_foreign = list(driver.calls)
        # Same-org control: the aggregator IS reached and the line comes back.
        with _tenant(org_a):
            own = LogHistoryQuery().astrolift_app_logs(_info(), app_slug=app_a.slug, since=since, until=until)
    finally:
        reset_log_query_driver_for_tests()

    assert foreign.items == []
    assert foreign.historical_available is False
    assert foreign.reason == ObservabilityPanelReason.NOT_CONFIGURED
    # The load-bearing assertion: nothing was fetched on the foreign path.
    assert calls_after_foreign == []

    # Control: the owning org still gets its logs (exactly one driver call,
    # from the same-org path only).
    assert [item.message for item in own.items] == ["ORG A SECRET LOG LINE"]
    assert len(driver.calls) == 1


# ---------------------------------------------------------------------------
# astroliftAppGoldenSignals — the Prometheus metric path
# ---------------------------------------------------------------------------


def test_golden_signals_foreign_org_slug_not_configured_and_never_hits_prometheus(
    permission_resolver, monkeypatch
):
    """Org B asks for Org A's app slug: NOT_CONFIGURED empty state, and
    Prometheus is never queried."""
    org_a, app_a = _scaffold("gsa", prometheus_endpoint="http://prom:9090")
    org_b, _ = _scaffold("gsb")
    permission_resolver.grant(Permission.APP_READ)

    def _boom(**kwargs):
        raise AssertionError("Prometheus must not be queried for a foreign org's app")

    monkeypatch.setattr(prom_client, "query_range_series", _boom)
    with _tenant(org_b):
        foreign = GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug=app_a.slug)
    assert foreign.reason == ObservabilityPanelReason.NOT_CONFIGURED
    assert foreign.signals == []

    # Control: same org reaches Prometheus and shapes the signal rows.
    monkeypatch.setattr(
        prom_client,
        "query_range_series",
        lambda **kw: [("", [(1700000000.0, 1.0), (1700000060.0, 2.0)])],
    )
    with _tenant(org_a):
        own = GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug=app_a.slug)
    assert own.reason == ObservabilityPanelReason.OK
    assert own.signals
