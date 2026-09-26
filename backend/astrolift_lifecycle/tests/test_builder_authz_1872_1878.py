"""Who may use the builder API, and on what (#1872, #1878).

#1872: create's explicit ``cluster_guid`` and promote's ``domain`` resolved
another org's cluster and managed domain. #1878: the routes checked org
membership and nothing else, so any member holding a token could run code on
the org's cluster and promote apps, whatever their role or the token's scopes.

Requests go through the full middleware stack with real API tokens and real
RoleBindings on roles taken from the system catalog; only Temporal is patched,
as in ``test_builder_api.py``. A browser session is refused outright.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_identity.api_tokens import CLI_DEVICE_SCOPES, DEFAULT_SCOPES, SCOPE_APP_ONBOARD, mint_token
from astrolift_identity.models import (
    ApiToken,
    Member,
    Organization,
    OrganizationModule,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.system_roles import SYSTEM_ROLES
from astrolift_lifecycle.models import AppEnvironment, DevEnvironment
from astrolift_registry.models import RegisteredApp

pytestmark = pytest.mark.django_db
User = get_user_model()

CREATE = "/api/builder/v1/dev-environments/"
FILES = "/api/builder/v1/dev-environments/{}/files/"
PROMOTE = "/api/builder/v1/dev-environments/{}/promote/"
FOREIGN_CLUSTER = {"detail": "dev environment is bound to a cluster outside this organization"}


@pytest.fixture(autouse=True)
def workflow_starts(monkeypatch):
    """Names of the workflows the routes started, in order."""
    from astrolift_workflows.client import WorkflowHandle

    starts: list[str] = []

    def _record(name, args, *, workflow_id, task_queue=None):
        starts.append(name)
        return WorkflowHandle(workflow_id=workflow_id, run_id=f"run-{len(starts)}", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _record)
    return starts


# ---- scaffolding ---------------------------------------------------------


def _org(slug: str) -> Organization:
    org = Organization.objects.create(name=slug, slug=slug)
    OrganizationModule.objects.create(
        organization=org, key=OrganizationModule.Key.CHAT_STUDIO_INTEGRATION, enabled=True
    )
    return org


def _cluster(org: Organization | None, slug: str) -> TenantCluster:
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="k8s-builder-authz",
        defaults={"name": "K8s", "plugin_version": "0.0.1", "capabilities_manifest": {}, "config_schema": {}},
    )
    return TenantCluster.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        provider_plugin=plugin,
        provider_config={},
        endpoint=f"https://{slug}.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        is_active=True,
    )


def _system_role(slug: str) -> Role:
    """The catalog's system role, upserted so a ``transaction=True`` flush
    elsewhere in the session cannot leave the seeded row missing."""
    _slug, level, name, description, permissions = next(row for row in SYSTEM_ROLES if row[0] == slug)
    role, _ = Role.objects.update_or_create(
        slug=slug,
        is_system=True,
        organization=None,
        defaults={
            "name": name,
            "description": description,
            "scope_level": level,
            "permissions": [p.value for p in permissions],
        },
    )
    return role


def _member(org: Organization, username: str, *grants) -> User:
    """An active member of ``org`` bound to ``(role, scope_kind, scope_id)`` grants.

    ``role`` is a system role slug or a ``Role``.
    """
    user = User.objects.create_user(username=username, email=f"{username}@astrolift.dev", password="pw")
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    for role, scope_kind, scope_id in grants:
        RoleBinding.objects.create(
            user=user,
            role=role if isinstance(role, Role) else _system_role(role),
            scope_kind=scope_kind,
            scope_id=scope_id,
        )
    return user


def _bearer(user, org: Organization, *, scopes=CLI_DEVICE_SCOPES, team: Team | None = None) -> dict:
    """Headers for a fresh API token; CLI device-flow scopes unless told otherwise."""
    minted = mint_token()
    ApiToken.objects.create(
        user=user,
        organization=org,
        team=team,
        name="builder-authz",
        token_hash=minted.token_hash,
        token_last_4=minted.last4,
        scopes=list(scopes),
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {minted.plaintext}"}


def _session(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _dev_env(org: Organization, cluster: TenantCluster | None, creator) -> DevEnvironment:
    return DevEnvironment.objects.create(
        organization=org,
        creator=creator,
        tenant_cluster=cluster,
        runtime=DevEnvironment.Runtime.PYTHON,
        port=8080,
        status=DevEnvironment.Status.RUNNING,
        namespace=f"builder-dev-{uuid.uuid4().hex[:12]}",
        files={"main.py": "print('original')"},
    )


def _send(method: str, path: str, payload: dict, headers: dict, client: Client | None = None):
    return getattr(client or Client(), method)(
        path, data=json.dumps(payload), content_type="application/json", **headers
    )


def _create(headers: dict, client: Client | None = None, **payload):
    return _send("post", CREATE, {"runtime": "python", **payload}, headers, client)


def _sync(dev: DevEnvironment, headers: dict):
    return _send("put", FILES.format(dev.guid), {"files": {"main.py": "print('synced')"}}, headers)


def _promote(dev: DevEnvironment, headers: dict, **payload):
    return _send("post", PROMOTE.format(dev.guid), {"app_name": "Shipped", **payload}, headers)


def _running(response) -> DevEnvironment:
    """The dev env a 201 created, moved on to ``running`` as provisioning would."""
    assert response.status_code == 201, response.content
    dev = DevEnvironment.objects.get(guid=response.json()["id"])
    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)
    return dev


def _untouched(dev: DevEnvironment) -> bool:
    dev.refresh_from_db()
    return dev.files == {"main.py": "print('original')"} and dev.status == DevEnvironment.Status.RUNNING


@pytest.fixture
def world():
    """Org A, where the callers act, and org B, another tenant."""
    a, b = _org("authz-a"), _org("authz-b")
    return SimpleNamespace(
        a=a,
        b=b,
        # Created first, so promote's default team.
        eng=Team.objects.create(organization=a, name="Eng", slug="eng"),
        ops=Team.objects.create(organization=a, name="Ops", slug="ops"),
        team_b=Team.objects.create(organization=b, name="B Eng", slug="b-eng"),
        cluster_a=_cluster(a, "authz-a-cluster"),
        cluster_b=_cluster(b, "authz-b-cluster"),
    )


@pytest.fixture
def admin(world):
    """An org admin of A with a CLI device-flow token: allowed everywhere in A."""
    user = _member(world.a, "authz-admin", ("org_admin", "ORG", world.a.id))
    return SimpleNamespace(user=user, headers=_bearer(user, world.a))


# ---- tenancy (#1872): a cross-org attempt on each route --------------------


def test_create_refuses_another_orgs_cluster_guid_as_if_it_did_not_exist(world, admin, workflow_starts):
    foreign = _create(admin.headers, cluster_guid=str(world.cluster_b.guid))
    missing = _create(admin.headers, cluster_guid=str(uuid.uuid4()))

    assert foreign.status_code == 404, foreign.content
    assert foreign.json() == missing.json() == {"detail": "cluster not found"}
    assert not DevEnvironment.objects.exists()
    assert workflow_starts == []


@pytest.mark.parametrize("owner", ["own", "shared"])
def test_create_binds_an_explicit_cluster_of_the_org_or_a_shared_one(world, admin, workflow_starts, owner):
    cluster = world.cluster_a if owner == "own" else _cluster(None, "authz-shared-cluster")

    response = _create(admin.headers, cluster_guid=str(cluster.guid))

    assert response.status_code == 201, response.content
    assert DevEnvironment.objects.get(guid=response.json()["id"]).tenant_cluster_id == cluster.pk
    assert workflow_starts == ["CreateDevEnvironmentWorkflow"]


@pytest.mark.parametrize(
    "shared_first", [True, False], ids=["shared registered first", "own registered first"]
)
def test_the_default_cluster_is_the_orgs_own_before_any_shared_one(workflow_starts, shared_first):
    """A tenant can publish a cluster as shared (#1918). While the org has a
    managed cluster of its own, the default never lands on a shared one,
    whichever has the lower pk."""
    org = _org("authz-default")
    if shared_first:
        shared = _cluster(None, "authz-default-shared")
        own = _cluster(org, "authz-default-own")
    else:
        own = _cluster(org, "authz-default-own")
        shared = _cluster(None, "authz-default-shared")
    assert (shared.pk < own.pk) is shared_first
    user = _member(org, "authz-default-admin", ("org_admin", "ORG", org.id))

    response = _create(_bearer(user, org))

    assert response.status_code == 201, response.content
    assert DevEnvironment.objects.get(guid=response.json()["id"]).tenant_cluster_id == own.pk


def test_the_default_cluster_falls_back_to_a_shared_one(workflow_starts):
    org = _org("authz-fallback")
    shared = _cluster(None, "authz-fallback-shared")
    user = _member(org, "authz-fallback-admin", ("org_admin", "ORG", org.id))

    response = _create(_bearer(user, org))

    assert response.status_code == 201, response.content
    assert DevEnvironment.objects.get(guid=response.json()["id"]).tenant_cluster_id == shared.pk


def test_sync_and_promote_answer_404_for_another_orgs_dev_environment(world, admin, workflow_starts):
    """Pinned, not changed: both routes already looked the dev env up in the caller's org."""
    theirs = _dev_env(
        world.b, world.cluster_b, _member(world.b, "authz-b-admin", ("org_admin", "ORG", world.b.id))
    )

    assert _sync(theirs, admin.headers).status_code == 404
    assert _promote(theirs, admin.headers).status_code == 404
    assert _untouched(theirs)
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == []


def test_sync_refuses_a_dev_environment_on_another_orgs_cluster(world, admin, workflow_starts):
    """The row the old create path made: org A's dev env on org B's cluster.
    It must not keep pushing code there."""
    dev = _dev_env(world.a, world.cluster_b, admin.user)

    response = _sync(dev, admin.headers)

    assert response.status_code == 409, response.content
    assert response.json() == FOREIGN_CLUSTER
    assert _untouched(dev)
    assert workflow_starts == []


def test_promote_refuses_a_dev_environment_on_another_orgs_cluster(world, admin, workflow_starts):
    dev = _dev_env(world.a, world.cluster_b, admin.user)

    response = _promote(dev, admin.headers)

    assert response.status_code == 409, response.content
    assert response.json() == FOREIGN_CLUSTER
    assert _untouched(dev)
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == []


def test_sync_and_promote_accept_a_dev_environment_on_a_shared_cluster(world, admin, workflow_starts):
    dev = _dev_env(world.a, _cluster(None, "authz-shared-cluster"), admin.user)

    assert _sync(dev, admin.headers).status_code == 200
    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)
    assert _promote(dev, admin.headers).status_code == 202
    assert workflow_starts == [
        "SyncDevEnvironmentFilesWorkflow",
        "OnboardAppWorkflow",
        "DeployPromotedAppWorkflow",
    ]


def test_promote_refuses_another_orgs_managed_domain_as_if_it_did_not_exist(world, admin, workflow_starts):
    ManagedDomain.objects.create(zone="b.example.test", organization=world.b, dns_driver="route53")
    dev = _dev_env(world.a, world.cluster_a, admin.user)

    foreign = _promote(dev, admin.headers, domain="b.example.test")

    assert foreign.status_code == 404, foreign.content
    assert foreign.json() == {"detail": "managed domain 'b.example.test' not found"}
    assert _untouched(dev)
    assert not RegisteredApp.objects.exists()
    assert not AppEnvironment.objects.exists()
    assert workflow_starts == []


@pytest.mark.parametrize("owner", ["own", "shared"])
def test_promote_binds_a_managed_domain_of_the_org_or_a_shared_one(world, admin, workflow_starts, owner):
    domain = ManagedDomain.objects.create(
        zone=f"{owner}.example.test", organization=world.a if owner == "own" else None, dns_driver="route53"
    )
    dev = _dev_env(world.a, world.cluster_a, admin.user)

    response = _promote(dev, admin.headers, domain=domain.zone)

    assert response.status_code == 202, response.content
    env = AppEnvironment.objects.get(registered_app__guid=response.json()["app_guid"])
    assert env.managed_domain_id == domain.pk


def test_promote_refuses_a_pending_managed_domain_as_if_it_did_not_exist(world, admin, workflow_starts):
    """A row still awaiting its TXT proof-of-control challenge is not
    registered yet, whether it is the caller's own org or not (#1931)."""
    domain = ManagedDomain.objects.create(
        zone="pending.example.test",
        organization=world.a,
        dns_driver="route53",
        verification_state=ManagedDomain.VerificationState.PENDING,
        verification_token="tok",
    )
    dev = _dev_env(world.a, world.cluster_a, admin.user)

    response = _promote(dev, admin.headers, domain=domain.zone)

    assert response.status_code == 404, response.content
    assert response.json() == {"detail": f"managed domain {domain.zone!r} not found"}
    assert _untouched(dev)
    assert not AppEnvironment.objects.exists()
    assert workflow_starts == []


def test_promote_answers_404_for_another_orgs_team(world, admin, workflow_starts):
    """Pinned, not changed: the team was already looked up in the caller's org."""
    dev = _dev_env(world.a, world.cluster_a, admin.user)

    response = _promote(dev, admin.headers, team_slug=world.team_b.slug)

    assert response.status_code == 404, response.content
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == []


# ---- token scope (#1878) -------------------------------------------------


@pytest.mark.parametrize(
    "scopes",
    [DEFAULT_SCOPES, (SCOPE_APP_ONBOARD,)],
    ids=["read-only enrollment token", "app-onboard only"],
)
def test_a_token_without_write_apps_is_refused_on_every_route(world, workflow_starts, scopes):
    """The user may do all of it; the token may not. ``app:onboard`` alone
    clears the scope ceiling for ``app.create``, so it pins the explicit
    ``write:apps`` requirement rather than the ceiling."""
    user = _member(world.a, "authz-scoped", ("org_admin", "ORG", world.a.id))
    headers = _bearer(user, world.a, scopes=scopes)
    dev = _dev_env(world.a, world.cluster_a, user)

    for response in (_create(headers), _sync(dev, headers), _promote(dev, headers)):
        assert response.status_code == 403, response.content
        assert response.json() == {
            "detail": "the api token lacks the write:apps scope",
            "reason": "missing_scope",
            "scope": "write:apps",
        }
    assert DevEnvironment.objects.count() == 1
    assert _untouched(dev)
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == []


def test_an_admin_scope_token_is_allowed(world, workflow_starts):
    user = _member(world.a, "authz-admin-scope", ("org_admin", "ORG", world.a.id))

    assert _create(_bearer(user, world.a, scopes=("admin",))).status_code == 201


# ---- permissions (#1878) -------------------------------------------------


@pytest.mark.parametrize(
    "grant",
    [None, ("org_auditor", "ORG"), ("team_developer", "TEAM")],
    ids=["member with no role", "org_auditor", "team_developer"],
)
def test_callers_without_app_create_are_refused_on_every_route(world, workflow_starts, grant):
    """``org_auditor`` reads everything; ``team_developer`` also deploys but
    creates nothing. A team role gets a token issued for its team, so its
    binding is in play on every route."""
    grants = ()
    team = None
    if grant is not None:
        role, scope_kind = grant
        team = world.eng if scope_kind == "TEAM" else None
        grants = ((role, scope_kind, team.id if team else world.a.id),)
    user = _member(world.a, "authz-denied", *grants)
    headers = _bearer(user, world.a, team=team)
    dev = _dev_env(world.a, world.cluster_a, user)

    create, sync, promote = _create(headers), _sync(dev, headers), _promote(dev, headers)

    for response in (create, sync, promote):
        assert response.status_code == 403, response.content
        assert response.json()["reason"] == "missing_permission"
        assert response.json()["permission"] == "app.create"
    where = "on the api token's team" if team else "in this organization"
    assert create.json()["detail"] == f"app.create is required {where}"
    assert promote.json()["detail"] == "app.create is required on team 'eng'"
    assert DevEnvironment.objects.count() == 1
    assert _untouched(dev)
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == []


def test_promote_needs_app_deploy_as_well_as_app_create(world, workflow_starts):
    creator = Role.objects.create(
        organization=world.a,
        name="Creator",
        slug="creator",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.create"],
    )
    user = _member(world.a, "authz-creator", (creator, "ORG", world.a.id))
    headers = _bearer(user, world.a)

    dev = _running(_create(headers))
    assert _sync(dev, headers).status_code == 200
    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)
    promoted = _promote(dev, headers)

    assert promoted.status_code == 403, promoted.content
    assert promoted.json() == {
        "detail": "app.deploy is required on team 'eng'",
        "reason": "missing_permission",
        "permission": "app.deploy",
    }
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == ["CreateDevEnvironmentWorkflow", "SyncDevEnvironmentFilesWorkflow"]


def test_a_grant_in_another_org_does_not_carry_over(world, workflow_starts):
    """An admin of org B who is a plain member of org A, on an org A token."""
    user = _member(world.a, "authz-b-admin-in-a", ("org_admin", "ORG", world.b.id))
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=world.b.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )

    response = _create(_bearer(user, world.a))

    assert response.status_code == 403, response.content
    assert response.json()["permission"] == "app.create"
    assert not DevEnvironment.objects.exists()


def test_a_team_admin_promotes_into_their_team_and_no_other(world, workflow_starts):
    """``team_admin`` on Eng holds app.create and app.deploy there only. A
    token issued for Eng must not stretch that to Ops."""
    user = _member(world.a, "authz-team-admin", ("team_admin", "TEAM", world.eng.id))

    for headers in (_bearer(user, world.a), _bearer(user, world.a, team=world.eng)):
        into_ops = _promote(_dev_env(world.a, world.cluster_a, user), headers, team_slug="ops")
        assert into_ops.status_code == 403, into_ops.content
        assert into_ops.json() == {
            "detail": "app.create is required on team 'ops'",
            "reason": "missing_permission",
            "permission": "app.create",
        }
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == []

    into_eng = _promote(_dev_env(world.a, world.cluster_a, user), _bearer(user, world.a), team_slug="eng")

    assert into_eng.status_code == 202, into_eng.content
    assert RegisteredApp.objects.get().team_id == world.eng.pk


def test_a_team_role_reaches_create_and_sync_through_a_token_for_its_team(world, workflow_starts):
    """Create and sync are checked at the org level; the org-wide token of a
    team admin does not reach them, a token issued for the team does."""
    user = _member(world.a, "authz-team-admin", ("team_admin", "TEAM", world.eng.id))

    org_wide = _create(_bearer(user, world.a))
    assert org_wide.status_code == 403, org_wide.content
    assert org_wide.json()["permission"] == "app.create"

    headers = _bearer(user, world.a, team=world.eng)
    dev = _running(_create(headers))
    assert _sync(dev, headers).status_code == 200
    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)
    assert _promote(dev, headers, team_slug="eng").status_code == 202


def test_a_token_for_a_deleted_team_is_refused(world, workflow_starts):
    """``team_admin`` on Eng with a token issued for Eng. Deleting Eng leaves
    both the binding and the token behind; neither may still create or sync."""
    user = _member(world.a, "authz-deleted-team", ("team_admin", "TEAM", world.eng.id))
    headers = _bearer(user, world.a, team=world.eng)
    dev = _dev_env(world.a, world.cluster_a, user)
    world.eng.soft_delete()

    for response in (_create(headers), _sync(dev, headers)):
        assert response.status_code == 403, response.content
        assert response.json() == {
            "detail": "app.create is required on the api token's team",
            "reason": "missing_permission",
            "permission": "app.create",
        }
    assert DevEnvironment.objects.count() == 1
    assert _untouched(dev)
    assert workflow_starts == []


def test_request_headers_do_not_widen_the_check(world, workflow_starts):
    """A team admin of Eng on an org-wide token names Eng in the team header.
    That lends nothing: create stays org-level and promote into Ops stays on
    Ops. A header naming another org is refused before the view runs, even
    for a promote this caller could otherwise make."""
    user = _member(world.a, "authz-header-spoof", ("team_admin", "TEAM", world.eng.id))
    team_header = {**_bearer(user, world.a), "HTTP_X_ASTROLIFT_TEAM": str(world.eng.pk)}
    org_header = {**_bearer(user, world.a), "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.b.guid)}

    created = _create(team_header)
    into_ops = _promote(_dev_env(world.a, world.cluster_a, user), team_header, team_slug="ops")
    into_b = _promote(_dev_env(world.a, world.cluster_a, user), org_header, team_slug="eng")

    assert created.status_code == 403, created.content
    assert created.json()["detail"] == "app.create is required in this organization"
    assert into_ops.status_code == 403, into_ops.content
    assert into_ops.json()["detail"] == "app.create is required on team 'ops'"
    assert into_b.status_code == 403, into_b.content
    assert into_b.json()["detail"] == "Selected organization does not match the API token's organization."
    assert DevEnvironment.objects.count() == 2
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == []


# ---- the permitted path ----------------------------------------------------


def test_an_org_admin_ships_end_to_end(world, admin, workflow_starts):
    dev = _running(_create(admin.headers))
    synced = _sync(dev, admin.headers)
    assert synced.status_code == 200, synced.content
    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)

    promoted = _promote(dev, admin.headers)

    assert promoted.status_code == 202, promoted.content
    app = RegisteredApp.objects.get(guid=promoted.json()["app_guid"])
    assert (app.organization_id, app.team_id, app.default_tenant_cluster_id) == (
        world.a.pk,
        world.eng.pk,
        world.cluster_a.pk,
    )
    assert workflow_starts == [
        "CreateDevEnvironmentWorkflow",
        "SyncDevEnvironmentFilesWorkflow",
        "OnboardAppWorkflow",
        "DeployPromotedAppWorkflow",
    ]


# ---- authentication ------------------------------------------------------


def test_a_session_is_refused_on_every_route(world, workflow_starts):
    """Only an API token authenticates. The routes are CSRF-exempt, CORS
    answers any origin with credentials, and previews serve user code on the
    builder's base domain, so a signed-in browser alone, an org owner's
    included, must not be enough to act."""
    owner = _member(world.a, "authz-session-owner", ("org_owner", "ORG", world.a.id))
    dev = _dev_env(world.a, world.cluster_a, owner)
    client = _session(owner)

    responses = (
        _create({}, client),
        _send("put", FILES.format(dev.guid), {"files": {"main.py": "print('synced')"}}, {}, client),
        _send("post", PROMOTE.format(dev.guid), {"app_name": "Shipped"}, {}, client),
    )

    for response in responses:
        assert response.status_code == 401, response.content
        assert response.json() == {
            "detail": "an api token is required: send Authorization: Bearer alft_at_...",
            "reason": "api_token_required",
        }
    assert DevEnvironment.objects.count() == 1
    assert _untouched(dev)
    assert not RegisteredApp.objects.exists()
    assert workflow_starts == []
