"""GraphQL reports unapplied role assignments instead of a ready binding (#1367).

Before #1444 an Azure keyless binding reported ready with no authorization at
all. After it the assignment is really attempted — but a binding whose
assignment is still propagating or was rejected still read as ready, which is
the hardest kind of failure to diagnose because nothing complains.

Real Postgres, no DB mocks.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, WorkloadIdentityGrant
from astrolift_services.schema.queries import ServicesQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_BLOB_ROLE = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _caller(username: str = "caller-1367"):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.test"},
    )
    return user


class _Graph:
    def __init__(self, org, app, env, service):
        self.org = org
        self.app = app
        self.env = env
        self.service = service


def _ctx(graph: _Graph):
    return tenant_context(TenantContext(organization_id=graph.org.id))


def _make_org_graph(suffix: str) -> _Graph:
    org = Organization.objects.create(name=f"Org {suffix}", slug=f"org-{suffix}-1367")
    team = Team.objects.create(organization=org, name=f"Team {suffix}", slug=f"team-{suffix}-1367")
    project = Project.objects.create(
        organization=org,
        team=team,
        name=f"Proj {suffix}",
        slug=f"proj-{suffix}-1367",
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Azure",
                slug="azure",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"aks-{suffix}-1367",
        name=f"AKS {suffix}",
        provider_plugin=ProviderPlugin.objects.get(slug="azure"),
        endpoint="https://example.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=f"App {suffix}",
        slug=f"app-{suffix}-1367",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    service = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.OBJECT_STORE,
        name="blobs",
        variant="object_store_blob_v2",
        status=ManagedService.Status.ACTIVE,
        config={},
    )
    return _Graph(org, app, env, service)


def _grant(graph: _Graph, *, state: str, scope: str = "", reason: str = "") -> WorkloadIdentityGrant:
    return WorkloadIdentityGrant.objects.create(
        managed_service=graph.service,
        app_environment=graph.env,
        provider_plugin_slug="azure",
        identity_role_name="astrolift-org-app",
        role_definition_id=_BLOB_ROLE,
        role_name="Storage Blob Data Contributor",
        scope=scope
        or f"/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/{graph.app.slug}",
        assignment_name="00000000-0000-0000-0000-000000000001",
        state=state,
        reason=reason,
    )


def _services(graph: _Graph):
    return ServicesQuery().astrolift_managed_services(_info(_caller()), app_slug=graph.app.slug)


def test_an_active_service_with_a_pending_assignment_is_not_ready(permission_resolver):
    a = _make_org_graph("a")
    _grant(a, state="pending", reason="the principal has not replicated yet")
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(a):
        (row,) = _services(a)

    assert row.status == ManagedService.Status.ACTIVE
    assert row.grant_state == "pending"
    assert row.binding_ready is False
    (grant,) = row.workload_identity_grants
    assert grant.state == "pending"
    assert "replicated" in grant.reason


def test_a_failed_assignment_outranks_a_pending_one(permission_resolver):
    a = _make_org_graph("a")
    _grant(a, state="pending", scope="/subscriptions/sub/resourceGroups/rg")
    _grant(a, state="failed", scope="/subscriptions/sub/resourceGroups/rg2", reason="RBAC denied")
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(a):
        (row,) = _services(a)

    assert row.grant_state == "failed"
    assert row.binding_ready is False


def test_a_binding_with_no_assignment_reads_ready_not_pending(permission_resolver):
    # Control-plane-only Azure drivers, and every AWS/GCP binding, resolve to no
    # role assignment at all. Requiring one would leave them unready forever.
    a = _make_org_graph("a")
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(a):
        (row,) = _services(a)

    assert row.grant_state == "not_required"
    assert row.binding_ready is True
    assert row.workload_identity_grants == []


def test_an_applied_assignment_reports_ready(permission_resolver):
    a = _make_org_graph("a")
    _grant(a, state="applied")
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(a):
        (row,) = _services(a)

    assert row.grant_state == "applied"
    assert row.binding_ready is True


def test_a_soft_deleted_grant_no_longer_holds_the_binding_back(permission_resolver):
    a = _make_org_graph("a")
    dropped = _grant(a, state="failed", reason="RBAC denied")
    dropped.soft_delete()
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(a):
        (row,) = _services(a)

    assert row.grant_state == "not_required"
    assert row.binding_ready is True


def test_grants_query_narrows_to_the_unapplied_ones(permission_resolver):
    a = _make_org_graph("a")
    _grant(a, state="applied", scope="/subscriptions/sub/resourceGroups/rg-ok")
    _grant(a, state="pending", scope="/subscriptions/sub/resourceGroups/rg-slow")
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(a):
        rows = ServicesQuery().astrolift_workload_identity_grants(
            _info(_caller()),
            app_slug=a.app.slug,
            unapplied_only=True,
        )

    assert [r.state for r in rows] == ["pending"]
    assert rows[0].environment_name == "production"


def test_grants_query_is_org_scoped(permission_resolver):
    a = _make_org_graph("a")
    b = _make_org_graph("b")
    _grant(a, state="failed", reason="RBAC denied")
    permission_resolver.grant(Permission.APP_READ)

    # A caller in org B naming org A's app must learn nothing about A's grants:
    # the scope string alone names A's subscription and resource group.
    with _ctx(b):
        leaked = ServicesQuery().astrolift_workload_identity_grants(
            _info(_caller()),
            app_slug=a.app.slug,
        )
    assert leaked == []

    with _ctx(a):
        mine = ServicesQuery().astrolift_workload_identity_grants(
            _info(_caller()),
            app_slug=a.app.slug,
        )
    assert [r.state for r in mine] == ["failed"]
