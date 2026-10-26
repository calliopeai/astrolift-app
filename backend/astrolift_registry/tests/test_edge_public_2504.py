"""The per-app public switch and the cluster policy over it (#2504).

The cluster allows public apps (``allow_public_apps``, on by default) and the
app asks (``edge_public``, off by default); only then does its platform route
on the Envoy edge go without the gate. Making an app public is a step-up
action that agents are refused; making it private never is. Only the
tenant cluster's API server is faked.
"""

from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from _sdk.cluster import ApplyError, ApplyResult

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import ActionSafetyRule, Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from core import edge_access, edge_public
from core.app_deploy import envoy_edge_routes
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from providers.k8s_native.edge_gateway import GATE_LABEL

pytestmark = pytest.mark.django_db

OIDC = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration",
    "client_id": "central-client-id",
    "auth_proxy_host": "auth.astro.example.net",
    "gateway_secret": "gw-secret-2504",
}
HOST = "pub.astro.example.net"


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture(autouse=True)
def _no_edge_workflow(monkeypatch):
    monkeypatch.setattr(edge_access, "reapply_edge", lambda cluster: True)


class _Api:
    """The edge namespace of one cluster: what it lists, what gets applied."""

    def __init__(self, routes=None, errors=None):
        self.routes: list[dict[str, Any]] = routes if routes is not None else []
        self.applied: list[tuple[str, list[dict[str, Any]]]] = []
        self.errors = errors or []

    def list_manifests(self, cluster, namespace, kind):
        return copy.deepcopy(self.routes) if kind.endswith("/HTTPRoute") else []

    def apply_manifests(self, cluster, namespace, manifests, *, dry_run=False):
        self.applied.append((namespace, list(manifests)))
        return ApplyResult(created=[], updated=[], unchanged=[], errors=list(self.errors))


@pytest.fixture
def api(monkeypatch):
    holder = _Api()
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: holder)
    return holder


def _world(*, ingress_class="envoy", oidc=OIDC, app_kind="", slug="pub"):
    org = Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}")
    team = Team.objects.create(organization=org, name="t", slug=f"t-{uuid.uuid4().hex[:6]}")
    project = Project.objects.create(organization=org, team=team, name="p", slug=f"p-{uuid.uuid4().hex[:6]}")
    app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name=slug,
        slug=slug,
        provisioning_status="ready",
        **({"app_kind": app_kind} if app_kind else {}),
    )
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws", defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}}
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:8]}",
        name="c",
        provider_plugin=plugin,
        provider_config={},
        region="us-west-2",
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        ingress_class=ingress_class,
        oidc_auth_config=oidc,
    )
    ns = f"{org.slug}-{slug}-prod"
    AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="prod", url=f"https://{HOST}", k8s_namespace=ns
    )
    return SimpleNamespace(org=org, app=app, cluster=cluster, ns=ns)


def _routes(world):
    return envoy_edge_routes(
        world.app, namespace=world.ns, workloads={"web": ([HOST], 8080)}, cluster=world.cluster, paused=False
    )


def _route(world):
    [route] = [m for m in _routes(world) if m["kind"] == "HTTPRoute"]
    return route


def _gated(route) -> bool:
    return route["metadata"]["labels"].get(GATE_LABEL) == "gated"


def _secret_set(route) -> bool:
    modifier = route["spec"]["rules"][0]["filters"][0]["requestHeaderModifier"]
    return any(h["value"] == OIDC["gateway_secret"] for h in modifier.get("set", []))


# ---- the fields ---------------------------------------------------------------


def test_defaults_keep_every_app_gated():
    world = _world()
    assert world.cluster.allow_public_apps is True
    assert world.app.edge_public is False
    route = _route(world)
    assert _gated(route) and _secret_set(route)


def test_both_fields_have_database_defaults_for_rolling_writers():
    assert TenantCluster._meta.get_field("allow_public_apps").db_default is True
    assert RegisteredApp._meta.get_field("edge_public").db_default is False


# ---- the render gate ---------------------------------------------------------------


def test_a_public_app_on_an_allowing_cluster_renders_ungated_and_without_the_secret():
    world = _world()
    world.app.edge_public = True
    route = _route(world)
    assert not _gated(route)
    assert not _secret_set(route)
    assert edge_public.public_state(world.app, world.cluster) == edge_public.PUBLIC


def test_a_refusing_cluster_keeps_a_public_app_gated():
    world = _world()
    world.app.edge_public = True
    world.cluster.allow_public_apps = False
    route = _route(world)
    assert _gated(route) and _secret_set(route)
    assert edge_public.public_state(world.app, world.cluster) == edge_public.CLUSTER_DISALLOWS


def test_an_access_list_keeps_a_public_app_gated():
    world = _world()
    world.app.edge_public = True
    world.app.edge_access = {"groups": ["staff"], "users": [], "source": "ui"}
    assert _gated(_route(world))
    assert edge_public.public_state(world.app, world.cluster) == edge_public.RESTRICTED


def test_a_brain_stays_gated(monkeypatch):
    world = _world(app_kind="mimwell")
    world.app.edge_public = True
    monkeypatch.setattr("core.edge_access.brain_fields", lambda app: {})
    assert _gated(_route(world))
    assert edge_public.public_state(world.app, world.cluster) == edge_public.BRAIN


def test_states_off_the_envoy_edge_and_without_a_gate():
    nginx = _world(ingress_class="nginx")
    nginx.app.edge_public = True
    assert edge_public.public_state(nginx.app, nginx.cluster) == edge_public.NOT_ENVOY
    open_cluster = _world(oidc={})
    assert edge_public.public_state(open_cluster.app, open_cluster.cluster) == edge_public.NO_GATE
    plain = _world(slug="plain")
    assert edge_public.public_state(plain.app, plain.cluster) == edge_public.GATED


# ---- moving the live routes ----------------------------------------------------------


def _live(world, *, gated=True):
    return [m for m in _routes(world) if m["kind"] == "HTTPRoute"] if gated else []


def test_making_public_reapplies_the_route_without_the_gate(api):
    world = _world()
    api.routes = _live(world)
    world.app.edge_public = True

    assert edge_public.set_edge_public(world.app, True) == []

    [(namespace, [route])] = api.applied
    assert namespace == "astrolift-edge"
    assert route["metadata"]["name"] == api.routes[0]["metadata"]["name"]
    assert route["spec"]["hostnames"] == [HOST]
    assert not _gated(route) and not _secret_set(route)


def test_making_private_again_puts_the_gate_and_secret_back(api):
    world = _world()
    world.app.edge_public = True
    world.app.save()
    api.routes = _live(world)
    assert not _gated(api.routes[0])

    assert edge_public.set_edge_public(world.app, False) == []

    [(_ns, [route])] = api.applied
    assert _gated(route) and _secret_set(route)


def test_reapply_leaves_other_routes_paused_environments_and_brain_token_routes_alone(api):
    world = _world()
    route = _live(world)[0]
    other_app = copy.deepcopy(route)
    other_app["metadata"]["labels"]["astrolift.dev/app"] = "someone-else"
    other_env = copy.deepcopy(route)
    other_env["metadata"]["labels"]["astrolift.dev/namespace"] = f"{world.ns}-staging"
    custom = copy.deepcopy(route)
    custom["metadata"]["labels"]["astrolift.dev/custom-domain"] = "pub.example.com"
    token_route = copy.deepcopy(route)
    token_route["spec"]["rules"][0]["matches"] = [{"path": {"type": "PathPrefix", "value": "/mcp"}}]
    api.routes = [other_app, other_env, custom, token_route]

    assert edge_public.set_edge_public(world.app, True) == []
    assert api.applied == []

    AppEnvironment.objects.filter(registered_app=world.app).update(ingress_paused=True)
    api.routes = [route]
    assert edge_public.reapply_app_routes(world.app) == []
    assert api.applied == []


def test_an_apply_failure_is_reported(api):
    world = _world()
    api.routes = _live(world)
    api.errors = [
        ApplyError(
            kind="HTTPRoute",
            name="x",
            namespace="astrolift-edge",
            exception_type="ApiException",
            exception_message="forbidden",
            is_retryable=False,
        )
    ]
    errors = edge_public.set_edge_public(world.app, True)
    assert len(errors) == 1 and world.cluster.slug in errors[0]
    world.app.refresh_from_db()
    assert world.app.edge_public is True


def test_restricting_a_public_app_makes_it_private_and_regates_it(api):
    world = _world()
    world.app.edge_public = True
    world.app.save()
    api.routes = _live(world)

    edge_access.set_app_access(world.app, groups=["staff"], users=[], source="ui")

    world.app.refresh_from_db()
    assert world.app.edge_public is False
    [(_ns, [route])] = api.applied
    assert _gated(route)


# ---- GraphQL: the switch -------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None), user=None))


def _switch(world, public, *, org=None):
    from astrolift_registry.schema.app_access import AppAccessMutation, SetAppEdgePublicInput

    with tenant_context(TenantContext(organization_id=(org or world.org).pk)):
        return AppAccessMutation().set_app_edge_public(
            _info(), SetAppEdgePublicInput(app_slug=world.app.slug, public=public)
        )


def test_the_switch_needs_app_access(permission_resolver, api):
    world = _world()
    permission_resolver.grant(Permission.APP_UPDATE)
    result = _switch(world, True)
    assert result.ok is False and result.errors[0].code == "PERMISSION_DENIED"
    world.app.refresh_from_db()
    assert world.app.edge_public is False


def test_the_switch_reports_the_state_per_cluster(permission_resolver, api):
    world = _world()
    permission_resolver.grant(Permission.APP_ACCESS)
    api.routes = _live(world)
    result = _switch(world, True)
    assert result.ok is True, result.errors
    assert result.data.edge_public is True
    assert [(s.cluster_slug, s.state) for s in result.data.public_states] == [(world.cluster.slug, "public")]


def test_making_public_asks_for_a_fresh_verification(permission_resolver, api, monkeypatch):
    import astrolift_identity.action_safety as safety
    from astrolift_graphql import failure

    world = _world()
    permission_resolver.grant(Permission.APP_ACCESS)
    monkeypatch.setattr(safety, "DEFAULT_ON", True)
    seen = []
    monkeypatch.setattr(
        "astrolift_identity.step_up.check_elevation",
        lambda info, **kw: seen.append(kw["action_label"]) or failure("STEP_UP_REQUIRED", "verify"),
    )

    refused = _switch(world, True)

    assert refused.ok is False and refused.errors[0].code == "STEP_UP_REQUIRED"
    assert seen == ["Make an app reachable without the edge login"]
    world.app.refresh_from_db()
    assert world.app.edge_public is False
    assert api.applied == []


def test_making_private_is_never_gated(permission_resolver, api, monkeypatch):
    import astrolift_identity.action_safety as safety
    from astrolift_graphql import failure

    world = _world()
    world.app.edge_public = True
    world.app.save()
    permission_resolver.grant(Permission.APP_ACCESS)
    monkeypatch.setattr(safety, "DEFAULT_ON", True)
    monkeypatch.setattr(
        "astrolift_identity.step_up.check_elevation", lambda info, **kw: failure("STEP_UP_REQUIRED", "verify")
    )

    result = _switch(world, False)

    assert result.ok is True, result.errors
    world.app.refresh_from_db()
    assert world.app.edge_public is False


def test_agents_are_refused_until_the_organization_allows_them(permission_resolver, api):
    from astrolift_registry.brain_manage import surface

    world = _world()
    permission_resolver.grant(Permission.APP_ACCESS)
    with surface("mcp"):
        refused = _switch(world, True)
    assert refused.ok is False and "needs a person" in refused.errors[0].message
    world.app.refresh_from_db()
    assert world.app.edge_public is False

    ActionSafetyRule.objects.create(
        organization=world.org, scope_kind="ORG", action="app.edge.make_public", allow_agents=True
    )
    with surface("mcp"):
        allowed = _switch(world, True)
    assert allowed.ok is True, allowed.errors


def test_an_access_list_or_a_brain_cannot_be_made_public(permission_resolver, api):
    permission_resolver.grant(Permission.APP_ACCESS)
    restricted = _world()
    restricted.app.edge_access = {"groups": ["staff"], "users": [], "source": "ui"}
    restricted.app.save()
    assert _switch(restricted, True).errors[0].code == "PRECONDITION"
    brain = _world(app_kind="mimwell", slug="kb")
    assert _switch(brain, True).errors[0].code == "PRECONDITION"


def test_another_orgs_app_is_not_found(permission_resolver, api):
    world = _world()
    permission_resolver.grant(Permission.APP_ACCESS)
    other = Organization.objects.create(name="O", slug=f"o-{uuid.uuid4().hex[:8]}")
    result = _switch(world, True, org=other)
    assert result.ok is False
    world.app.refresh_from_db()
    assert world.app.edge_public is False
    assert api.applied == []


def test_an_edge_failure_is_not_reported_as_success(permission_resolver, api):
    world = _world()
    permission_resolver.grant(Permission.APP_ACCESS)
    api.routes = _live(world)
    api.errors = [
        ApplyError(
            kind="HTTPRoute",
            name="x",
            namespace="astrolift-edge",
            exception_type="ApiException",
            exception_message="forbidden",
            is_retryable=False,
        )
    ]
    result = _switch(world, True)
    assert result.ok is False and "next deploy" in result.errors[0].message


# ---- GraphQL: the cluster policy -----------------------------------------------------


def _update_cluster(world, *, org=None, **kwargs):
    from astrolift_clusters.schema.mutations import ClustersMutation, UpdateTenantClusterInput

    with tenant_context(TenantContext(organization_id=(org or world.org).pk)):
        return ClustersMutation().update_tenant_cluster(
            _info(), UpdateTenantClusterInput(id=GUID(str(world.cluster.guid)), **kwargs)
        )


def test_turning_the_cluster_policy_off_regates_its_public_apps_at_once(permission_resolver, api):
    world = _world()
    world.app.edge_public = True
    world.app.save()
    private = _world(slug="private")
    AppEnvironment.objects.filter(registered_app=private.app).update(tenant_cluster=world.cluster)
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    api.routes = _live(world)
    assert not _gated(api.routes[0])

    result = _update_cluster(world, allow_public_apps=False)

    assert result.ok is True, result.errors
    assert result.data.allow_public_apps is False
    world.cluster.refresh_from_db()
    assert world.cluster.allow_public_apps is False
    [(_ns, [route])] = api.applied
    assert route["metadata"]["labels"]["astrolift.dev/app"] == "pub"
    assert _gated(route) and _secret_set(route)


def test_leaving_the_cluster_policy_alone_touches_no_routes(permission_resolver, api):
    world = _world()
    world.app.edge_public = True
    world.app.save()
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    assert _update_cluster(world, region="us-east-1").ok is True
    assert _update_cluster(world, allow_public_apps=True).ok is True
    assert api.applied == []


def test_the_cluster_policy_needs_cluster_update_in_the_owning_org(permission_resolver, api):
    world = _world()
    permission_resolver.grant(Permission.APP_ACCESS)
    assert _update_cluster(world, allow_public_apps=False).ok is False
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    other = Organization.objects.create(name="O", slug=f"o-{uuid.uuid4().hex[:8]}")
    assert _update_cluster(world, org=other, allow_public_apps=False).ok is False
    world.cluster.refresh_from_db()
    assert world.cluster.allow_public_apps is True


def test_the_access_query_reads_the_switch(permission_resolver):
    from astrolift_registry.schema.app_access import AppAccessQuery

    world = _world()
    world.app.edge_public = True
    world.app.save()
    world.cluster.allow_public_apps = False
    world.cluster.save()
    permission_resolver.grant(Permission.APP_READ)
    with tenant_context(TenantContext(organization_id=world.org.pk)):
        access = AppAccessQuery().astrolift_app_access(_info(), app_slug=world.app.slug)
    assert access.edge_public is True
    assert [(s.cluster_slug, s.state) for s in access.public_states] == [
        (world.cluster.slug, "cluster_disallows")
    ]
