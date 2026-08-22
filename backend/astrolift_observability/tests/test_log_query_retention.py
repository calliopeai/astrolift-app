"""``astroliftAppLogs`` reports the platform's own log retention.

``reached_retention`` used to come only from the aggregator driver — Loki
reports nothing and the CloudWatch driver hardcodes ``False`` — so a
window that had already aged out read as "no lines in window" instead of
"earlier lines were discarded". The resolver now resolves the org's
effective log retention and badges the page itself.
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC
from types import SimpleNamespace

import pytest
from _sdk.log_stream import LogPage

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability.schema.log_queries import LogHistoryQuery
from astrolift_operations.observability_retention import PLATFORM_DEFAULTS
from astrolift_registry.models import RegisteredApp
from core.cluster_log_query import (
    reset_log_query_driver_for_tests,
    set_log_query_driver_for_tests,
)
from core.permissions import Permission
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


@pytest.fixture
def scaffold():
    org = Organization.objects.create(name="Acme Ret", slug="acme-ret")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-ret")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-ret")
    plugin = ProviderPlugin.objects.create(
        name="K8s",
        slug="k8s-ret",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug="prod-ret",
        provider_plugin=plugin,
        provider_config={
            "log_driver": "loki",
            "log_config": {"endpoint": "http://loki:3100"},
        },
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello Ret",
        slug="hello-ret",
        k8s_namespace="acme-hello",
        provisioning_status="ready",
        default_tenant_cluster=cluster,
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://hello-ret.example.com",
    )
    return org, app


class _FakeDriver:
    def __init__(self, page: LogPage):
        self.page = page

    def query_logs(self, query, since, until, *, limit, cursor, level, search):
        return self.page


def _query(org, app, *, days_back: int, driver_reached: bool = False):
    set_log_query_driver_for_tests(_FakeDriver(LogPage(items=[], reached_retention=driver_reached)))
    try:
        with tenant_context(TenantContext(organization_id=org.id)):
            return LogHistoryQuery().astrolift_app_logs(
                _info(),
                app_slug=app.slug,
                since=dt.datetime.now(UTC) - dt.timedelta(days=days_back),
                until=dt.datetime.now(UTC),
            )
    finally:
        reset_log_query_driver_for_tests()


def test_window_inside_retention_is_not_badged(scaffold, permission_resolver):
    org, app = scaffold
    permission_resolver.grant(Permission.APP_READ_LOGS)
    page = _query(org, app, days_back=1)
    assert page.reached_retention is False


def test_window_past_the_org_retention_is_badged(scaffold, permission_resolver):
    """The aggregator answers happily; only the platform knows the
    window has aged out of the org's retention."""
    org, app = scaffold
    org.log_retention_days_default = 7
    org.save(update_fields=["log_retention_days_default"])
    permission_resolver.grant(Permission.APP_READ_LOGS)
    page = _query(org, app, days_back=14)
    assert page.reached_retention is True


def test_org_override_above_the_window_clears_the_badge(scaffold, permission_resolver):
    org, app = scaffold
    org.log_retention_days_default = 90
    org.save(update_fields=["log_retention_days_default"])
    permission_resolver.grant(Permission.APP_READ_LOGS)
    page = _query(org, app, days_back=20)
    assert page.reached_retention is False


def test_platform_default_applies_without_an_override(scaffold, permission_resolver):
    """Fresh org: the column carries the platform default, so a window
    one day past it is badged and one day inside it is not."""
    org, app = scaffold
    default_days = PLATFORM_DEFAULTS["log"]
    assert org.log_retention_days_default == default_days
    permission_resolver.grant(Permission.APP_READ_LOGS)
    assert _query(org, app, days_back=default_days + 1).reached_retention is True
    assert _query(org, app, days_back=default_days - 1).reached_retention is False


def test_driver_reported_retention_still_wins(scaffold, permission_resolver):
    org, app = scaffold
    permission_resolver.grant(Permission.APP_READ_LOGS)
    page = _query(org, app, days_back=1, driver_reached=True)
    assert page.reached_retention is True
