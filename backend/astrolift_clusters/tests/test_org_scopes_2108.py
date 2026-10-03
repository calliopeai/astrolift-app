"""Every cluster/domain surface refuses grants below its organization owner."""

from __future__ import annotations

import datetime as dt
import inspect
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone

from astrolift_clusters.models import ClusterBootstrapRun, ManagedDomain, ProviderPluginConfig, TenantCluster
from astrolift_clusters.schema import auth_users as auth
from astrolift_clusters.schema import mutations as mut
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_clusters.scopes import cluster_catalog_org_scope, cluster_org_scope, domain_org_scope
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_graphql import GUID
from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Organization
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db

QUERY_PERMISSIONS = {
    Permission.CLUSTER_REGISTER: (
        "astrolift_clusters",
        "astrolift_clusters_page",
        "astrolift_cluster",
        "astrolift_provider_regions",
        "astrolift_cluster_lifecycle_audit",
        "astrolift_recent_cluster_workflows",
        "astrolift_app_count_for_cluster",
        "astrolift_cluster_health",
        "astrolift_cluster_live_state",
        "astrolift_cluster_prometheus_metrics",
        "astrolift_cluster_prometheus_range_metrics",
        "astrolift_cluster_system_metrics",
        "astrolift_cluster_workload_health",
    ),
    Permission.APP_CREATE: ("astrolift_cluster_count",),
    Permission.PROVIDER_PLUGIN_READ: (
        "astrolift_managed_domains",
        "astrolift_dns_zones",
        "astrolift_dns_certificates",
    ),
    Permission.CLUSTER_UPDATE: ("astrolift_cognito_user_pools", "astrolift_cognito_user_pool_clients"),
    Permission.CLUSTER_MANAGE: ("astrolift_cluster_bootstrap_plan",),
    Permission.APP_DEPLOY: ("astrolift_cluster_certificates",),
}
MUTATION_PERMISSIONS = {
    Permission.CLUSTER_REGISTER: ("register_tenant_cluster",),
    Permission.CLUSTER_MANAGE: (
        "issue_cluster_agent_key",
        "deploy_cluster_agent",
        "reconcile_cluster_ingresses",
        "bring_cluster_into_management",
        "refresh_cluster_management",
        "install_cluster_prereqs",
        "record_cluster_bootstrap_run",
        "provision_managed_domain",
        "revalidate_managed_domain",
        "reissue_managed_domain_cert",
    ),
    Permission.CLUSTER_UPDATE: ("update_tenant_cluster",),
    Permission.CLUSTER_UNREGISTER: ("decommission_cluster", "unregister_tenant_cluster"),
    Permission.PROVIDER_PLUGIN_CONFIGURE: (
        "create_managed_domain",
        "update_managed_domain",
        "soft_delete_managed_domain",
        "verify_managed_domain",
        "configure_provider_plugin",
    ),
}
AUTH_MUTATIONS = (
    "create_cluster_auth_user",
    "set_cluster_auth_user_password",
    "reset_cluster_auth_user_password",
    "set_cluster_auth_user_enabled",
    "delete_cluster_auth_user",
    "set_cluster_auth_user_groups",
    "create_cluster_auth_group",
)
ROUTES = (
    [
        (cls, name, permission)
        for cls, groups in ((ClustersQuery, QUERY_PERMISSIONS), (mut.ClustersMutation, MUTATION_PERMISSIONS))
        for permission, names in groups.items()
        for name in names
    ]
    + [(auth.ClusterAuthUsersQuery, "astrolift_cluster_auth_users", Permission.CLUSTER_USERS)]
    + [(auth.ClusterAuthUsersMutation, name, Permission.CLUSTER_USERS) for name in AUTH_MUTATIONS]
)
assert len(ROUTES) == 48
ROUTE_IDS = [name for _, name, _ in ROUTES]
PERMISSIONS = tuple({permission for _, _, permission in ROUTES})


@pytest.fixture
def world(monkeypatch):
    world = ScopeWorld("clusters-2108")
    world.cluster = make_cluster(world, "clusters-2108")
    world.cluster.lifecycle = TenantCluster.Lifecycle.MANAGED.value
    world.cluster.save(update_fields=["lifecycle", "updated_at", "version"])
    world.domain = ManagedDomain.objects.create(
        organization=world.org, zone="own-2108.test", dns_driver="route53"
    )
    world.user = make_user("clusters-2108")
    world.other_org = Organization.objects.create(name="Other", slug="other-2108")
    world.queued = []

    def start(*args, **kwargs):
        world.queued.append((args, kwargs))
        return SimpleNamespace(enqueued=True)

    def signal(*args, **kwargs):
        world.queued.append((args, kwargs))
        return True

    monkeypatch.setattr(mut, "start_workflow", start)
    monkeypatch.setattr(mut, "signal_workflow", signal)
    return world


def _tenant(world, *, selected="own", actor=True):
    team = world.medops if selected == "own" else world.platform
    project = world.medops_project if selected == "own" else world.platform_project
    return tenant_context(
        TenantContext(
            organization_id=world.org.pk,
            actor_user_id=world.user.pk if actor else None,
            team_id=team.pk,
            project_id=project.pk,
        )
    )


def _grant(world, kind="ORG", *, foreign=False):
    scope_id = (
        world.other_org.pk
        if foreign
        else {
            "ORG": world.org.pk,
            "TEAM": world.medops.pk,
            "PROJECT": world.medops_project.pk,
        }[kind]
    )
    bind_role(
        world.user, permissions=PERMISSIONS, kind=kind, scope_id=scope_id, slug=f"grant-{uuid.uuid4().hex}"
    )


def _business_rows():
    return [
        list(model.all_objects.order_by("pk").values())
        for model in (
            TenantCluster,
            ManagedDomain,
            ProviderPluginConfig,
            ClusterBootstrapRun,
        )
    ]


def _kwargs(world, name, *, cluster=None, domain=None):
    cluster = cluster or world.cluster
    domain = domain or world.domain
    cluster_id = GUID(str(cluster.guid))
    domain_id = GUID(str(domain.guid))
    now = timezone.now()
    inputs = {
        "register_tenant_cluster": lambda: mut.RegisterTenantClusterInput(
            slug="new-2108",
            name="New",
            provider_plugin_slug=cluster.provider_plugin.slug,
            auth_method="kubeconfig",
        ),
        "issue_cluster_agent_key": lambda: mut.IssueClusterAgentKeyInput(cluster_id=cluster_id),
        "deploy_cluster_agent": lambda: mut.DeployClusterAgentInput(cluster_id=cluster_id),
        "update_tenant_cluster": lambda: mut.UpdateTenantClusterInput(id=cluster_id, region="new-region"),
        "reconcile_cluster_ingresses": lambda: mut.ReconcileClusterIngressesInput(cluster_id=cluster_id),
        "bring_cluster_into_management": lambda: mut.BringClusterIntoManagementInputType(
            cluster_id=cluster_id
        ),
        "refresh_cluster_management": lambda: mut.RefreshClusterManagementInputType(cluster_id=cluster_id),
        "decommission_cluster": lambda: mut.DecommissionClusterInputType(cluster_id=cluster_id),
        "install_cluster_prereqs": lambda: mut.InstallClusterPrereqsInputType(
            cluster_id=cluster_id, selected_components=[]
        ),
        "record_cluster_bootstrap_run": lambda: mut.RecordClusterBootstrapRunInput(
            cluster_slug=cluster.slug,
            status="succeeded",
            chart_version="1",
            installed_releases=[],
            cli_version="1",
            host_info={},
            started_at=now - dt.timedelta(seconds=1),
            ended_at=now,
        ),
        "unregister_tenant_cluster": lambda: mut.UnregisterTenantClusterInput(id=cluster_id),
        "create_managed_domain": lambda: mut.CreateManagedDomainInput(
            zone="new-2108.test", dns_driver="route53", dns_config={"zone_id": "owned"}
        ),
        "update_managed_domain": lambda: mut.UpdateManagedDomainInput(id=domain_id, is_wildcard_managed=True),
        "soft_delete_managed_domain": lambda: mut.SoftDeleteManagedDomainInput(id=domain_id),
        "verify_managed_domain": lambda: mut.VerifyManagedDomainInput(zone=domain.zone),
        "configure_provider_plugin": lambda: mut.ConfigureProviderPluginInput(
            plugin_slug=cluster.provider_plugin.slug, config={"test": True}
        ),
        "create_cluster_auth_user": lambda: auth.CreateClusterAuthUserInput(
            cluster_id=cluster_id, email="valid@example.test"
        ),
        "set_cluster_auth_user_password": lambda: auth.SetClusterAuthUserPasswordInput(
            cluster_id=cluster_id, username="user", password="dummy-test-password"
        ),
        "reset_cluster_auth_user_password": lambda: auth.ClusterAuthUserRefInput(
            cluster_id=cluster_id, username="user"
        ),
        "set_cluster_auth_user_enabled": lambda: auth.SetClusterAuthUserEnabledInput(
            cluster_id=cluster_id, username="user", enabled=True
        ),
        "delete_cluster_auth_user": lambda: auth.ClusterAuthUserRefInput(
            cluster_id=cluster_id, username="user"
        ),
        "set_cluster_auth_user_groups": lambda: auth.SetClusterAuthUserGroupsInput(
            cluster_id=cluster_id, username="user", add=["group"]
        ),
        "create_cluster_auth_group": lambda: auth.CreateClusterAuthGroupInput(
            cluster_id=cluster_id, name="group"
        ),
    }
    if name in inputs:
        return {"input": inputs[name]()}
    if name in ("provision_managed_domain", "revalidate_managed_domain", "reissue_managed_domain_cert"):
        return {"cluster_id": cluster_id, "zone": domain.zone}
    cls = next(cls for cls, candidate, _ in ROUTES if candidate == name)
    args = inspect.signature(getattr(cls, name)).parameters
    values = {
        "cluster_id": cluster_id,
        "slug": cluster.slug,
        "provider_plugin_slug": cluster.provider_plugin.slug,
        "pool_id": "pool",
        "dns_driver": "route53",
    }
    return {key: value for key, value in values.items() if key in args}


def _invoke(world, route, **overrides):
    cls, name, _ = route
    return getattr(cls(), name)(make_info(world.user), **_kwargs(world, name, **overrides))


def _denied(world, route, **overrides):
    cls, _, _ = route
    if cls in (ClustersQuery, auth.ClusterAuthUsersQuery):
        with pytest.raises(PermissionDenied):
            _invoke(world, route, **overrides)
    else:
        result = _invoke(world, route, **overrides)
        assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
@pytest.mark.parametrize(
    "binding,selected,foreign",
    [("TEAM", "own", False), ("TEAM", "sibling", False), ("PROJECT", "own", False), ("ORG", "own", True)],
)
def test_team_project_and_foreign_org_grants_open_no_org_resources(world, route, binding, selected, foreign):
    _grant(world, binding, foreign=foreign)
    before = _business_rows()
    with _tenant(world, selected=selected):
        _denied(world, route)
    assert _business_rows() == before
    assert world.queued == []


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_organization_grant_reaches_own_resources_with_selected_sibling_team(world, route):
    _grant(world)
    with _tenant(world, selected="sibling"):
        result = _invoke(world, route)
    # Unavailable drivers can report a precondition; the scoped gate must
    # admit the org owner regardless of the request's selected team.
    if hasattr(result, "ok") and not result.ok:
        assert all(
            error.code in {"PRECONDITION", "VALIDATION", "NOT_FOUND"} for error in result.errors
        ), result


@contextmanager
def _token(world, *, team=None, scopes=None, org=None):
    Member.objects.get_or_create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    token = ApiToken.objects.create(
        user=world.user,
        organization=org or world.org,
        team=team,
        name="scope2108",
        token_hash=uuid.uuid4().hex,
        scopes=scopes if scopes is not None else ["admin"],
    )
    handle = set_current_api_token(token)
    try:
        yield token
    finally:
        reset_current_api_token(handle)


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
@pytest.mark.parametrize("operator", [False, True])
def test_team_bearer_ceiling_refuses_even_org_owner_and_operator_admin_tokens(world, route, operator):
    _grant(world)
    if operator:
        world.user.is_superuser = True
        world.user.save(update_fields=["is_superuser"])
    before = _business_rows()
    with _tenant(world), _token(world, team=world.medops):
        _denied(world, route)
    assert _business_rows() == before
    assert world.queued == []


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_no_actor_opens_nothing_despite_selected_team(world, route):
    _grant(world)
    with _tenant(world, actor=False):
        _denied(world, route)


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_org_bearer_permission_ceiling_still_applies(world, route):
    _grant(world)
    with _tenant(world), _token(world, scopes=[]):
        _denied(world, route)


@pytest.mark.parametrize(
    "factory",
    [
        cluster_catalog_org_scope(Permission.CLUSTER_MANAGE),
        cluster_org_scope(Permission.CLUSTER_MANAGE),
        domain_org_scope(Permission.PROVIDER_PLUGIN_CONFIGURE),
    ],
)
def test_scope_misses_never_fall_back_to_selected_team(world, factory):
    _grant(world, "TEAM")
    with _tenant(world):
        assert factory({}) == PermissionScope(kind=ScopeKind.ORG, id=world.org.pk)
        assert factory(
            {"cluster_id": str(uuid.uuid4()), "input": SimpleNamespace(id=str(uuid.uuid4()))}
        ) == PermissionScope(kind=ScopeKind.ORG, id=world.org.pk)


@pytest.mark.parametrize(
    "name", ["provision_managed_domain", "revalidate_managed_domain", "reissue_managed_domain_cert"]
)
def test_owned_cluster_cannot_be_used_to_write_shared_domain(world, name):
    _grant(world)
    shared = ManagedDomain.objects.create(organization=None, zone="shared-2108.test", dns_driver="route53")
    before = _business_rows()
    route = next(route for route in ROUTES if route[1] == name)
    with _tenant(world):
        _denied(world, route, domain=shared)
    assert _business_rows() == before
    assert world.queued == []


def test_tenant_cannot_append_shared_cluster_bootstrap_history(world):
    _grant(world)
    world.cluster.organization = None
    world.cluster.save(update_fields=["organization", "updated_at", "version"])
    route = next(route for route in ROUTES if route[1] == "record_cluster_bootstrap_run")
    with _tenant(world):
        _denied(world, route)
    assert not ClusterBootstrapRun.objects.exists()


SHARED_ROUTES = [route for route in ROUTES if route[0] != ClustersQuery]


def _shared_kwargs(world, name):
    kwargs = _kwargs(world, name)
    if name in {"register_tenant_cluster", "create_managed_domain", "configure_provider_plugin"}:
        kwargs["input"].organization_scoped = False
    return kwargs


@pytest.mark.parametrize("route", SHARED_ROUTES, ids=[name for _, name, _ in SHARED_ROUTES])
def test_org_grant_cannot_write_shared_resources_or_read_shared_identity_users(world, route):
    _grant(world)
    world.cluster.organization = None
    world.cluster.save(update_fields=["organization", "updated_at", "version"])
    world.domain.organization = None
    world.domain.save(update_fields=["organization", "updated_at", "version"])
    before = _business_rows()
    cls, name, _ = route
    with _tenant(world):
        if cls == auth.ClusterAuthUsersQuery:
            with pytest.raises(PermissionDenied):
                getattr(cls(), name)(make_info(world.user), **_shared_kwargs(world, name))
        else:
            result = getattr(cls(), name)(make_info(world.user), **_shared_kwargs(world, name))
            assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result
    assert _business_rows() == before
    assert world.queued == []


@pytest.mark.parametrize(
    "name",
    [
        "issue_cluster_agent_key",
        "record_cluster_bootstrap_run",
        "provision_managed_domain",
        "revalidate_managed_domain",
        "reissue_managed_domain_cert",
        "update_managed_domain",
        "configure_provider_plugin",
    ],
)
def test_active_operator_org_admin_token_can_write_shared_resources(world, name):
    world.user.is_superuser = True
    world.user.save(update_fields=["is_superuser"])
    world.cluster.organization = None
    world.cluster.save(update_fields=["organization", "updated_at", "version"])
    world.domain.organization = None
    world.domain.save(update_fields=["organization", "updated_at", "version"])
    with _tenant(world), _token(world):
        result = getattr(mut.ClustersMutation(), name)(make_info(world.user), **_shared_kwargs(world, name))
    assert result.ok, result
    if name in {"provision_managed_domain", "revalidate_managed_domain", "reissue_managed_domain_cert"}:
        assert len(world.queued) == 1
    elif name == "record_cluster_bootstrap_run":
        assert ClusterBootstrapRun.objects.get().organization_id == world.org.pk


def test_org_bearer_admin_token_allows_own_cluster_write(world):
    _grant(world)
    route = next(route for route in ROUTES if route[1] == "issue_cluster_agent_key")
    with _tenant(world), _token(world):
        result = _invoke(world, route)
    assert result.ok, result
    world.cluster.refresh_from_db()
    assert world.cluster.agent_key_hash


@pytest.mark.parametrize("name", ["issue_cluster_agent_key", "deploy_cluster_agent"])
@pytest.mark.parametrize("shared", [False, True])
def test_withdrawn_bearer_membership_refuses_legacy_agent_writes_before_effects(world, name, shared):
    _grant(world)
    world.user.is_superuser = shared
    world.user.save(update_fields=["is_superuser"])
    if shared:
        world.cluster.organization = None
        world.cluster.save(update_fields=["organization", "updated_at", "version"])
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk, is_active=False)
    before = _business_rows()
    with _tenant(world), _token(world):
        result = getattr(mut.ClustersMutation(), name)(make_info(world.user), **_kwargs(world, name))
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result
    assert _business_rows() == before
    assert world.queued == []


def test_foreign_org_bearer_cannot_reuse_an_active_org_role(world):
    _grant(world)
    with _tenant(world), _token(world, org=world.other_org):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_clusters(make_info(world.user))


TARGET_ROUTES = [
    route
    for route in ROUTES
    if route[1]
    not in {
        "astrolift_clusters",
        "astrolift_clusters_page",
        "astrolift_cluster_count",
        "astrolift_managed_domains",
        "astrolift_provider_regions",
        "astrolift_dns_zones",
        "astrolift_dns_certificates",
        "register_tenant_cluster",
        "create_managed_domain",
        "configure_provider_plugin",
    }
]


@pytest.mark.parametrize("route", TARGET_ROUTES, ids=[name for _, name, _ in TARGET_ROUTES])
def test_org_owner_cannot_resolve_other_org_targets(world, route):
    _grant(world)
    foreign = TenantCluster.objects.create(
        organization=world.other_org,
        provider_plugin=world.cluster.provider_plugin,
        name="Foreign secret cluster",
        slug="foreign-2108",
        endpoint="https://foreign.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        provider_config={},
    )
    domain = ManagedDomain.objects.create(
        organization=world.other_org, zone="foreign-2108.test", dns_driver="route53"
    )
    before = _business_rows()
    with _tenant(world):
        result = _invoke(world, route, cluster=foreign, domain=domain)
    if route[0] not in (ClustersQuery, auth.ClusterAuthUsersQuery):
        assert not result.ok and result.errors[0].code == "NOT_FOUND", result
    else:
        assert foreign.name not in repr(result) and foreign.slug not in repr(result)
    assert _business_rows() == before
    assert world.queued == []


def test_org_inventory_contains_only_owned_and_shared_live_resources(world):
    _grant(world)
    shared = TenantCluster.objects.create(
        organization=None,
        provider_plugin=world.cluster.provider_plugin,
        name="Shared",
        slug="shared-2108",
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        endpoint="",
        auth_method="kubeconfig",
        auth_config={},
        provider_config={},
    )
    foreign = TenantCluster.objects.create(
        organization=world.other_org,
        provider_plugin=world.cluster.provider_plugin,
        name="Foreign",
        slug="foreign-2108",
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        endpoint="",
        auth_method="kubeconfig",
        auth_config={},
        provider_config={},
    )
    shared_domain = ManagedDomain.objects.create(
        organization=None, zone="shared-2108.test", dns_driver="route53"
    )
    ManagedDomain.objects.create(organization=world.other_org, zone="foreign-2108.test", dns_driver="route53")
    deleted = ManagedDomain.objects.create(
        organization=world.org, zone="deleted-2108.test", dns_driver="route53"
    )
    deleted.soft_delete()
    with _tenant(world, selected="sibling"):
        clusters = ClustersQuery().astrolift_clusters(make_info(world.user))
        page = ClustersQuery().astrolift_clusters_page(make_info(world.user))
        domains = ClustersQuery().astrolift_managed_domains(make_info(world.user))
        count = ClustersQuery().astrolift_cluster_count(make_info(world.user))
    expected = {str(world.cluster.guid), str(shared.guid)}
    assert {str(row.id) for row in clusters} == expected
    assert {str(row.id) for row in page.items} == expected
    assert str(foreign.guid) not in expected
    assert {str(row.id) for row in domains} == {str(world.domain.guid), str(shared_domain.guid)}
    assert count == 2


@pytest.mark.parametrize("team_bound,operator", [(True, False), (True, True), (False, False)])
def test_http_bearer_ceiling_prevents_agent_key_rotation(world, team_bound, operator):
    _grant(world)
    world.user.is_superuser = operator
    world.user.save(update_fields=["is_superuser"])
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    issued = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops if team_bound else None,
        name="http2108",
        token_hash=issued.token_hash,
        scopes=["admin"],
    )
    before = world.cluster.agent_key_hash
    response = Client().post(
        f"/{settings.BASE_URL}gql/config/",
        data={
            "query": "mutation($input: IssueClusterAgentKeyInput!) { issueClusterAgentKey(input: $input) { ok errors { code } } }",
            "variables": {"input": {"clusterId": str(world.cluster.guid)}},
        },
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {issued.plaintext}",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_ASTROLIFT_TEAM=str(world.platform.pk),
    )
    assert response.status_code == 200, response.content
    result = response.json()["data"]["issueClusterAgentKey"]
    world.cluster.refresh_from_db()
    if team_bound:
        assert result == {"ok": False, "errors": [{"code": "PERMISSION_DENIED"}]}
        assert world.cluster.agent_key_hash == before
    else:
        assert result["ok"] and world.cluster.agent_key_hash != before


@pytest.mark.parametrize("kind", ["TEAM", "ORG"])
def test_http_selected_team_cannot_grant_cluster_inventory_upward(world, kind):
    _grant(world, kind)
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    client = Client()
    client.force_login(world.user)
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        data={"query": "{ astroliftClusters { id } }"},
        content_type="application/json",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_ASTROLIFT_TEAM=str(world.medops.pk),
        HTTP_X_PLATFORM="WEB",
    )
    assert response.status_code == 200, response.content
    payload = response.json()
    if kind == "TEAM":
        assert payload.get("errors")
        assert payload.get("data") is None
    else:
        assert not payload.get("errors"), payload
        assert payload["data"]["astroliftClusters"] == [{"id": str(world.cluster.guid)}]
