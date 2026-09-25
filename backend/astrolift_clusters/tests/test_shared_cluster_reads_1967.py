"""Tenant live reads of a shared (org-NULL) cluster (#1967).

A shared cluster runs every org's workloads in the platform's cloud account.
Workload health listed every org's namespaces (and every namespace at all for
an org with no app there), system metrics accepted any namespace and
defaulted to the cluster's first, recent workflows showed the platform's
install/bootstrap runs, and the Cognito and certificate pickers listed the
platform account's pools and certificate domains.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from core.cluster_observability import namespace_for_app
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, g: None))


@pytest.fixture
def plugin():
    [row] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="local", slug=f"local-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    return row


@pytest.fixture
def shared(plugin):
    return TenantCluster.objects.create(
        organization=None,
        slug=f"shared-{uuid.uuid4().hex[:6]}",
        name="shared",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


def _org(tag):
    return Organization.objects.create(name=tag, slug=f"{tag}-{uuid.uuid4().hex[:6]}")


def _app_on(org, cluster, slug):
    team = Team.objects.create(organization=org, name="t", slug=f"t-{uuid.uuid4().hex[:6]}")
    app = RegisteredApp.objects.create(
        organization=org, team=team, name=slug, slug=slug, provisioning_status="ready"
    )
    AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="prod", required_approvals=0
    )
    return namespace_for_app(app)


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))


def _operator():
    return User.objects.create(username=f"root-{uuid.uuid4().hex[:6]}", is_superuser=True)


def _capture(monkeypatch, name, result):
    from core import cluster_management

    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(cluster_management, name, fake)
    return calls


def test_workload_health_shows_a_tenant_only_its_own_namespaces(shared, permission_resolver, monkeypatch):
    a, b = _org("a"), _org("b")
    ns_a = _app_on(a, shared, "web")
    _app_on(b, shared, "api")
    calls = _capture(monkeypatch, "cluster_workload_health_dispatch", [])
    permission_resolver.grant(Permission.CLUSTER_REGISTER)

    with tenant_context(TenantContext(organization_id=a.id)):
        ClustersQuery().astrolift_cluster_workload_health(_info(), cluster_id=GUID(str(shared.guid)))

    assert calls == [{"cluster": shared, "namespaces": [ns_a]}]


def test_workload_health_never_asks_for_every_namespace_for_an_org_with_no_app(
    shared, permission_resolver, monkeypatch
):
    a, b = _org("a"), _org("b")
    _app_on(b, shared, "api")
    calls = _capture(monkeypatch, "cluster_workload_health_dispatch", [{"namespace": "x"}])
    permission_resolver.grant(Permission.CLUSTER_REGISTER)

    with tenant_context(TenantContext(organization_id=a.id)):
        rows = ClustersQuery().astrolift_cluster_workload_health(_info(), cluster_id=GUID(str(shared.guid)))

    assert rows == []
    assert calls == []


def test_the_operator_still_sees_the_whole_shared_cluster(shared, permission_resolver, monkeypatch):
    a, b = _org("a"), _org("b")
    ns_a, ns_b = _app_on(a, shared, "web"), _app_on(b, shared, "api")
    calls = _capture(monkeypatch, "cluster_workload_health_dispatch", [])
    permission_resolver.grant(Permission.CLUSTER_REGISTER)

    with tenant_context(TenantContext(organization_id=a.id)):
        ClustersQuery().astrolift_cluster_workload_health(
            _info(_operator()), cluster_id=GUID(str(shared.guid))
        )

    [call] = calls
    assert set(call["namespaces"]) == {"astrolift-system", ns_a, ns_b}


def test_recent_workflows_and_account_pickers_are_empty_for_a_tenant(
    shared, permission_resolver, monkeypatch
):
    a = _org("a")
    monkeypatch.setattr(
        "astrolift_workflows.client.list_workflows_for_cluster",
        lambda guid, limit=10: [{"workflow_id": "InstallClusterPrereqsWorkflow-x"}],
    )
    pools = _capture(monkeypatch, "cognito_user_pools_dispatch", [{"pool_id": "p"}])
    certs = _capture(monkeypatch, "cluster_certificates_dispatch", {"supported": True, "certificates": []})
    for perm in Permission:
        permission_resolver.grant(perm)

    with tenant_context(TenantContext(organization_id=a.id)):
        runs = ClustersQuery().astrolift_recent_cluster_workflows(_info(), cluster_id=GUID(str(shared.guid)))
        user_pools = ClustersQuery().astrolift_cognito_user_pools(_info(), cluster_id=GUID(str(shared.guid)))
        certificates = ClustersQuery().astrolift_cluster_certificates(
            _info(), cluster_id=GUID(str(shared.guid))
        )

    assert runs == []
    assert user_pools == []
    assert certificates.supported is False
    assert pools == [] and certs == []
