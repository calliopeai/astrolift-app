"""A dev environment belongs to its creator and a team (#1919).

Before this, sync and promote checked only that *some* ``app.create``
holder in the org was asking — the guid is in the preview hostname, so
any co-worker who could create a dev environment anywhere could take
over another member's running one. This module pins the fix's actual
authorization matrix, built on ``ScopeWorld`` so the team-scope shape
matches every other sub-org permission test in the suite:

* a team member holding ``app.create`` on their own team can create there.
* a plain teammate on the same team, holding ``app.create`` too but
  neither the creator nor a team admin, cannot sync or promote another
  member's dev environment.
* a team admin (holds ``team.manage_members`` on that team) can.
* the same admin-tier grant on a *different* team cannot.
* an org-scope holder can, same as everywhere else in the RBAC chain.
* a row with no resolvable team (backfilled null, #1919's own migration)
  is org-admin-only — not even its own creator passes without one.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.test import Client

from astrolift_identity.api_tokens import CLI_DEVICE_SCOPES, mint_token
from astrolift_identity.models import ApiToken, Member, OrganizationModule
from astrolift_lifecycle.models import DevEnvironment
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_user

pytestmark = pytest.mark.django_db

CREATE = "/api/builder/v1/dev-environments/"
FILES = "/api/builder/v1/dev-environments/{}/files/"
PROMOTE = "/api/builder/v1/dev-environments/{}/promote/"


@pytest.fixture(autouse=True)
def _no_temporal(monkeypatch):
    from astrolift_workflows.client import WorkflowHandle

    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda name, args, *, workflow_id, task_queue=None: WorkflowHandle(
            workflow_id=workflow_id, run_id="run-1", enqueued=True
        ),
    )


@pytest.fixture
def world():
    w = ScopeWorld("devenv1919")
    OrganizationModule.objects.create(
        organization=w.org, key=OrganizationModule.Key.CHAT_STUDIO_INTEGRATION, enabled=True
    )
    w.cluster = make_cluster(w, "devenv1919")
    return w


def _user(suffix: str, org):
    """A real, org-member user (#1910): the bearer flow refuses a token
    whose owner has no active ``Member`` row in the token's organization,
    which ``make_user`` alone does not set up."""
    user = make_user(suffix)
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    return user


def _bearer(user, org) -> dict:
    minted = mint_token()
    ApiToken.objects.create(
        user=user,
        organization=org,
        name="ownership-1919",
        token_hash=minted.token_hash,
        token_last_4=minted.last4,
        scopes=list(CLI_DEVICE_SCOPES),
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {minted.plaintext}"}


def _send(method: str, path: str, payload: dict, headers: dict):
    return getattr(Client(), method)(
        path, data=json.dumps(payload), content_type="application/json", **headers
    )


def _create(headers: dict, **payload):
    return _send("post", CREATE, {"runtime": "python", **payload}, headers)


def _sync(dev: DevEnvironment, headers: dict):
    return _send("put", FILES.format(dev.guid), {"files": {"main.py": "print('taken over')"}}, headers)


def _promote(dev: DevEnvironment, headers: dict, **payload):
    return _send("post", PROMOTE.format(dev.guid), {"app_name": "Taken Over", **payload}, headers)


def _running_dev_env(world, *, creator, team) -> DevEnvironment:
    """A dev environment as ``create`` would leave it, minus the round trip:
    directly via the model so each test can pick the row's team (including
    ``None``, for the pre-#1919 backfill case create itself can no longer
    produce)."""
    return DevEnvironment.objects.create(
        organization=world.org,
        team=team,
        creator=creator,
        tenant_cluster=world.cluster,
        runtime=DevEnvironment.Runtime.PYTHON,
        port=8080,
        status=DevEnvironment.Status.RUNNING,
        namespace=f"builder-dev-{uuid.uuid4().hex[:12]}",
        files={"main.py": "print('original')"},
    )


def _untouched(dev: DevEnvironment) -> bool:
    dev.refresh_from_db()
    return dev.files == {"main.py": "print('original')"} and dev.status == DevEnvironment.Status.RUNNING


# ---- create: a team member reaches their own team ------------------------


def test_a_team_member_can_create_in_their_own_team(world):
    owner = _user("devenv1919-owner", world.org)
    bind_role(
        owner,
        permissions=[Permission.APP_CREATE],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="devenv1919-owner-role",
    )

    response = _create(
        _bearer(owner, world.org), team_slug=world.medops.slug, cluster_guid=str(world.cluster.guid)
    )

    assert response.status_code == 201, response.content
    dev = DevEnvironment.objects.get(guid=response.json()["id"])
    assert dev.team_id == world.medops.id
    assert dev.creator_id == owner.id


# ---- sync/promote: creator, teammate, team admin, other team, org admin --


def test_a_teammate_without_admin_cannot_sync_another_members_env(world):
    owner = _user("devenv1919-o2", world.org)
    teammate = _user("devenv1919-mate", world.org)
    for user, slug in ((owner, "devenv1919-o2-role"), (teammate, "devenv1919-mate-role")):
        bind_role(user, permissions=[Permission.APP_CREATE], kind="TEAM", scope_id=world.medops.id, slug=slug)
    dev = _running_dev_env(world, creator=owner, team=world.medops)

    response = _sync(dev, _bearer(teammate, world.org))

    assert response.status_code == 403, response.content
    assert response.json() == {
        "detail": "only the dev environment's creator or a team admin may do this",
        "reason": "not_owner",
    }
    assert _untouched(dev)


def test_the_creator_can_sync_their_own_env(world):
    owner = _user("devenv1919-o3", world.org)
    bind_role(
        owner,
        permissions=[Permission.APP_CREATE],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="devenv1919-o3-role",
    )
    dev = _running_dev_env(world, creator=owner, team=world.medops)

    response = _sync(dev, _bearer(owner, world.org))

    assert response.status_code == 200, response.content
    dev.refresh_from_db()
    assert dev.files == {"main.py": "print('taken over')"}


def test_a_team_admin_can_sync_a_members_env(world):
    owner = _user("devenv1919-o4", world.org)
    admin = _user("devenv1919-admin", world.org)
    bind_role(
        owner,
        permissions=[Permission.APP_CREATE],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="devenv1919-o4-role",
    )
    bind_role(
        admin,
        permissions=[Permission.APP_CREATE, Permission.TEAM_MANAGE_MEMBERS],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="devenv1919-admin-role",
    )
    dev = _running_dev_env(world, creator=owner, team=world.medops)

    response = _sync(dev, _bearer(admin, world.org))

    assert response.status_code == 200, response.content
    dev.refresh_from_db()
    assert dev.files == {"main.py": "print('taken over')"}


def test_another_teams_admin_cannot_sync(world):
    owner = _user("devenv1919-o5", world.org)
    other_admin = _user("devenv1919-other-admin", world.org)
    bind_role(
        owner,
        permissions=[Permission.APP_CREATE],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="devenv1919-o5-role",
    )
    bind_role(
        other_admin,
        permissions=[Permission.APP_CREATE, Permission.TEAM_MANAGE_MEMBERS],
        kind="TEAM",
        scope_id=world.platform.id,
        slug="devenv1919-other-admin-role",
    )
    dev = _running_dev_env(world, creator=owner, team=world.medops)

    response = _sync(dev, _bearer(other_admin, world.org))

    assert response.status_code == 403, response.content
    assert response.json()["reason"] == "missing_permission"
    assert response.json()["permission"] == "app.create"
    assert _untouched(dev)


def test_an_org_admin_can_sync_anyones_env(world):
    owner = _user("devenv1919-o6", world.org)
    org_admin = _user("devenv1919-org-admin", world.org)
    bind_role(
        owner,
        permissions=[Permission.APP_CREATE],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="devenv1919-o6-role",
    )
    bind_role(
        org_admin, permissions=list(Permission), kind="ORG", scope_id=world.org.id, slug="devenv1919-org-role"
    )
    dev = _running_dev_env(world, creator=owner, team=world.medops)

    response = _sync(dev, _bearer(org_admin, world.org))

    assert response.status_code == 200, response.content


def test_a_team_admin_can_promote_a_members_env_but_not_take_it_to_another_team(world):
    """The dev environment's own team gates ownership; the destination team
    named in the promote body still gates where the app lands, unchanged."""
    owner = _user("devenv1919-o7", world.org)
    admin = _user("devenv1919-admin2", world.org)
    bind_role(
        owner,
        permissions=[Permission.APP_CREATE],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="devenv1919-o7-role",
    )
    bind_role(
        admin,
        permissions=[Permission.APP_CREATE, Permission.APP_DEPLOY, Permission.TEAM_MANAGE_MEMBERS],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="devenv1919-admin2-role",
    )
    dev = _running_dev_env(world, creator=owner, team=world.medops)

    into_platform = _promote(dev, _bearer(admin, world.org), team_slug=world.platform.slug)
    assert into_platform.status_code == 403, into_platform.content
    assert into_platform.json()["permission"] == "app.create"
    assert not RegisteredApp.objects.filter(slug="taken-over").exists()

    into_medops = _promote(dev, _bearer(admin, world.org), team_slug=world.medops.slug)
    assert into_medops.status_code == 202, into_medops.content
    assert RegisteredApp.objects.get(slug="taken-over").team_id == world.medops.id


def test_a_null_team_row_is_org_admin_only_even_for_its_own_creator(world):
    """Backfill (#1919's migration) leaves a team it cannot derive as
    ``None`` rather than guessing, and a null team is org-admin-only: even
    the row's own creator, without an org-scope grant, is refused."""
    owner = _user("devenv1919-o8", world.org)
    org_admin = _user("devenv1919-org-admin2", world.org)
    bind_role(
        owner,
        permissions=[Permission.APP_CREATE],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="devenv1919-o8-role",
    )
    bind_role(
        org_admin,
        permissions=list(Permission),
        kind="ORG",
        scope_id=world.org.id,
        slug="devenv1919-org-role2",
    )
    dev = _running_dev_env(world, creator=owner, team=None)

    denied = _sync(dev, _bearer(owner, world.org))
    assert denied.status_code == 403, denied.content
    assert denied.json() == {
        "detail": "app.create is required in this organization",
        "reason": "missing_permission",
        "permission": "app.create",
    }
    assert _untouched(dev)

    allowed = _sync(dev, _bearer(org_admin, world.org))
    assert allowed.status_code == 200, allowed.content
