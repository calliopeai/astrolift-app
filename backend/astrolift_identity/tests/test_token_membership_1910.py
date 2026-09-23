"""A credential stops working when its owner leaves the token's org (#1910).

SCIM deprovisioning deactivates the ORG ``Member`` row. It keeps the
account active while the person belongs to another org, and it keeps
their RoleBindings, so before #1910 an ``alft_at_`` bearer minted for
the org they left kept acting there on every surface.

The rule now lives in ``api_tokens.verify_token``, which both bearer
entry points share: the HTTP middleware (GraphQL, REST, builder API,
MCP gateway) and the exec WebSocket relay. The device-flow refresh
applies the same rule, and SCIM deprovisioning also revokes what the
person held so re-adding them does not bring an old token back.

Most tests below switch the membership off directly rather than through
SCIM. That is how a deprovision from before this fix, or any path other
than SCIM, leaves things: nothing is revoked, so the per-request check
is the only thing standing between the old token and the org.
"""

from __future__ import annotations

import json
import uuid
from importlib import import_module
from io import StringIO
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client

from astrolift_agents.models import AgentTask
from astrolift_identity import device_flow
from astrolift_identity.api_tokens import DEFAULT_SCOPES, mint_token, verify_token
from astrolift_identity.models import ApiToken, DeviceFlowSession, Member, Organization, Team
from astrolift_lifecycle import deploy_tokens
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.schema.exec_ws import exec_ws_application
from core.schema.vnc_ws import vnc_ws_application
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db
User = get_user_model()

REFUSED = {"detail": "invalid or expired api token"}


# ---- world -----------------------------------------------------------


def _person(username: str, *orgs: Organization):
    """A user who is an active member of ``orgs`` with the same org role in each."""
    user = User.objects.create_user(username=username, email=f"{username}@example.test")
    for org in orgs:
        Member.objects.create(
            user=user,
            scope_kind=Member.ScopeKind.ORG,
            scope_id=org.pk,
            is_active=True,
            lifecycle=Member.Lifecycle.ACTIVE,
        )
        bind_role(
            user,
            permissions=[Permission.TEAM_READ, Permission.AGENT_TASK_WATCH],
            kind="ORG",
            scope_id=org.pk,
            slug=f"member-1910-{username}-{org.slug}",
        )
    return user


@pytest.fixture
def world():
    # ``acme`` is created first so it is Dana's lowest-id membership, the
    # org the WebSocket session paths infer while she still belongs to it.
    acme = Organization.objects.create(name="Acme", slug="acme-1910")
    globex = Organization.objects.create(name="Globex", slug="globex-1910")
    Team.objects.create(organization=acme, name="Payments", slug="payments-1910")
    Team.objects.create(organization=globex, name="Research", slug="research-1910")
    return SimpleNamespace(
        acme=acme,
        globex=globex,
        dana=_person("dana-1910", acme, globex),
        eli=_person("eli-1910", acme),
    )


def _token(user, org, *, scopes=DEFAULT_SCOPES) -> str:
    minted = mint_token()
    ApiToken.objects.create(
        user=user,
        organization=org,
        name="1910",
        token_hash=minted.token_hash,
        token_last_4=minted.last4,
        scopes=list(scopes),
    )
    return minted.plaintext


def _drop_membership(user, org) -> None:
    Member.objects.filter(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=org.pk).update(
        is_active=False, lifecycle=Member.Lifecycle.DEACTIVATED
    )


# ---- surfaces --------------------------------------------------------


def _graphql(bearer: str):
    return Client().post(
        f"/{settings.BASE_URL}gql/config/",
        data=json.dumps({"query": "{ astroliftTeams { slug } }"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {bearer}",
    )


def _team_slugs(response) -> list[str]:
    assert response.status_code == 200, response.content
    body = response.json()
    assert not body.get("errors"), body
    return [team["slug"] for team in body["data"]["astroliftTeams"]]


def _builder(bearer: str):
    # An unsupported runtime fails validation before anything is
    # provisioned, so an accepted bearer gets a 4xx from the view, never
    # the middleware's 401.
    return Client().post(
        "/api/builder/v1/dev-environments/",
        data=json.dumps({"runtime": "cobol"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {bearer}",
    )


def _mcp(bearer: str):
    return Client().post(
        "/api/mcp/v1/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1"},
                },
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {bearer}",
        HTTP_ACCEPT="application/json, text/event-stream",
    )


def _accepted(surface: str, response) -> bool:
    if surface == "graphql":
        return _team_slugs(response) == ["payments-1910"]
    if surface == "builder":
        return response.status_code != 401
    return response.status_code == 200 and response.json()["result"]["protocolVersion"] == "2025-11-25"


def _websocket(application, path: str, headers, monkeypatch, target_resolver: str):
    """Drive a WebSocket handshake, recording the org whose target it looks up.

    The target resolver is replaced by a recorder that finds nothing, so
    a handshake that gets past authentication and the permission gate
    closes as not-found, and one refused earlier never reaches it.
    """
    looked_up: list[int] = []

    async def resolve(**kwargs):
        looked_up.append(kwargs["tenant_org_id"])
        return None

    monkeypatch.setattr(target_resolver, resolve)
    sent: list[dict] = []

    async def receive():
        pytest.fail("a handshake that finds no target must not open a relay")

    async def send(message):
        sent.append(message)

    async_to_sync(application)({"type": "websocket", "path": path, "headers": headers}, receive, send)
    return looked_up, sent


def _exec(bearer: str, monkeypatch):
    return _websocket(
        exec_ws_application,
        "/app/exec/payments-api/web",
        [(b"authorization", f"Bearer {bearer}".encode())],
        monkeypatch,
        "core.schema.exec_ws._resolve_exec_target",
    )


def _close(code: int) -> list[dict]:
    return [{"type": "websocket.close", "code": code}]


# ---- the token check, on every bearer surface ------------------------


@pytest.mark.parametrize("surface", ["graphql", "builder", "mcp"])
def test_http_surfaces_refuse_the_token_once_its_owner_left_the_org(world, surface):
    call = {"graphql": _graphql, "builder": _builder, "mcp": _mcp}[surface]
    bearer = _token(world.dana, world.acme)
    assert _accepted(surface, call(bearer))

    _drop_membership(world.dana, world.acme)

    response = call(bearer)
    assert response.status_code == 401
    assert response.json() == REFUSED


def test_exec_relay_refuses_the_token_once_its_owner_left_the_org(world, monkeypatch):
    bearer = _token(world.dana, world.acme)
    assert _exec(bearer, monkeypatch) == ([world.acme.pk], _close(4404))

    _drop_membership(world.dana, world.acme)

    assert _exec(bearer, monkeypatch) == ([], _close(4401))


def test_other_members_and_the_owners_other_orgs_keep_working(world, monkeypatch):
    dana_globex = _token(world.dana, world.globex)
    eli_acme = _token(world.eli, world.acme)

    _drop_membership(world.dana, world.acme)

    assert _team_slugs(_graphql(dana_globex)) == ["research-1910"]
    assert _mcp(dana_globex).status_code == 200
    assert _team_slugs(_graphql(eli_acme)) == ["payments-1910"]
    assert _builder(eli_acme).status_code != 401
    assert _exec(eli_acme, monkeypatch) == ([world.acme.pk], _close(4404))


def _soft_delete_membership(world):
    Member.objects.get(user=world.dana, scope_kind=Member.ScopeKind.ORG, scope_id=world.acme.pk).soft_delete()


def _deactivate_account(world):
    world.dana.is_active = False
    world.dana.save(update_fields=["is_active"])


@pytest.mark.parametrize(
    "ending",
    [
        pytest.param(lambda w: _drop_membership(w.dana, w.acme), id="membership-deactivated"),
        pytest.param(_soft_delete_membership, id="membership-soft-deleted"),
        pytest.param(_deactivate_account, id="account-deactivated"),
        pytest.param(lambda w: w.acme.soft_delete(), id="org-soft-deleted"),
    ],
)
def test_verify_token_refuses_every_way_the_membership_ends(world, ending):
    bearer = _token(world.dana, world.acme)
    assert verify_token(bearer) is not None

    ending(world)

    assert verify_token(bearer) is None


@pytest.mark.parametrize("superuser", [False, True], ids=["user", "superuser"])
def test_a_token_needs_a_membership_even_for_a_superuser(world, superuser):
    """Tokens are org credentials. A superuser outside the org gets no
    exemption, matching the device-flow approval, which already offers
    only orgs the approver is an active member of."""
    outsider = User.objects.create_user(
        username=f"outsider-1910-{superuser}", email="outsider-1910@example.test", is_superuser=superuser
    )
    bearer = _token(outsider, world.acme)

    assert verify_token(bearer) is None
    assert _graphql(bearer).status_code == 401


# ---- the device-flow refresh -----------------------------------------


def _cli_login(user, org):
    """Approve and collect a CLI device login; return its credentials."""
    row, session_id = device_flow.create_session(client_label="astro-cli")
    assert device_flow.approve_session(row, user=user, organization=org) is None
    result = device_flow.poll_complete(session_id)
    assert result.status == "issued"
    return result.credentials


def _refresh(refresh_token: str):
    return Client().post(
        "/api/cli/v1/auth/refresh",
        data=json.dumps({"refresh_token": refresh_token}),
        content_type="application/json",
    )


def test_refresh_ends_the_chain_once_the_approver_left_the_org(world):
    dana = _cli_login(world.dana, world.acme)
    eli = _cli_login(world.eli, world.acme)

    _drop_membership(world.dana, world.acme)

    refused = _refresh(dana.refresh_token)
    assert refused.status_code == 410
    assert refused.json()["code"] == "expired_token"
    chain = DeviceFlowSession.objects.get(approved_user=world.dana)
    assert chain.refresh_token_hash == ""
    assert chain.api_token.is_revoked is True
    # The chain stays ended: the same refresh token is simply unknown now.
    assert _refresh(dana.refresh_token).status_code == 401

    rotated = _refresh(eli.refresh_token)
    assert rotated.status_code == 200
    assert _team_slugs(_graphql(rotated.json()["access_token"])) == ["payments-1910"]


# ---- SCIM deprovisioning and re-adding -------------------------------


def _scim(org: Organization) -> str:
    out = StringIO()
    call_command("issue_scim_token", "--org", org.slug, stdout=out)
    for line in out.getvalue().splitlines():
        if line.strip().startswith("token:"):
            return line.split("token:", 1)[1].strip()
    raise AssertionError(f"command printed no token: {out.getvalue()!r}")


def _scim_set_active(org: Organization, user, active: bool) -> None:
    member = Member.objects.get(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=org.pk)
    response = Client().patch(
        f"/api/scim/v2/Users/{member.guid}",
        data=json.dumps({"Operations": [{"op": "replace", "path": "active", "value": active}]}),
        content_type="application/scim+json",
        HTTP_AUTHORIZATION=f"Bearer {_scim(org)}",
    )
    assert response.status_code == 200, response.content


def test_scim_deprovision_revokes_what_the_person_held_and_readding_does_not_revive_it(world):
    """Decision: re-adding a person does not bring back their old credentials.

    Offboarding can be for cause, and a token copied somewhere during the
    first tenure should not quietly start working again when the IdP
    re-adds the person. They sign in again and get new ones.
    """
    token = _token(world.dana, world.acme)
    cli = _cli_login(world.dana, world.acme)
    waiting_row, waiting_id = device_flow.create_session(client_label="approved, not yet polled")
    assert device_flow.approve_session(waiting_row, user=world.dana, organization=world.acme) is None
    enrollment = device_flow.create_enrollment(user=world.dana, organization=world.acme)
    elsewhere = _token(world.dana, world.globex)

    _scim_set_active(world.acme, world.dana, False)

    assert ApiToken.objects.get(user=world.dana, organization=world.acme, name="1910").is_revoked is True
    # Cleared rather than refused: 401 (unknown chain), not the 410 a
    # still-live chain gets from the membership check.
    assert _refresh(cli.refresh_token).status_code == 401
    assert device_flow.poll_complete(waiting_id).status == "expired"
    assert device_flow.consume_enrollment(enrollment.token_plaintext).status == "unknown"
    assert _team_slugs(_graphql(elsewhere)) == ["research-1910"]

    _scim_set_active(world.acme, world.dana, True)

    assert _graphql(token).status_code == 401
    assert _graphql(cli.access_token).status_code == 401
    assert _refresh(cli.refresh_token).status_code == 401
    assert _team_slugs(_graphql(_token(world.dana, world.acme))) == ["payments-1910"]


# ---- deploy tokens: the app's credential, not a person's -------------


def _app(org: Organization) -> RegisteredApp:
    team = Team.objects.filter(organization=org).first()
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="Payments API",
        slug="payments-api-1910",
        k8s_namespace="acme-payments-api-1910",
        provisioning_status="ready",
    )


def _deploy_status(bearer: str):
    return Client().get(
        f"/api/cli/v1/deployments/{uuid.uuid4()}/status/",
        HTTP_AUTHORIZATION=f"Bearer {bearer}",
    )


@pytest.mark.parametrize(
    "ending,still_works",
    [
        pytest.param(lambda w, app: _scim_set_active(w.acme, w.eli, False), True, id="creator-offboarded"),
        pytest.param(lambda w, app: app.soft_delete(), False, id="app-soft-deleted"),
        pytest.param(lambda w, app: w.acme.soft_delete(), False, id="org-soft-deleted"),
    ],
)
def test_deploy_token_outlives_its_creator_but_not_its_app_or_org(world, ending, still_works):
    """Decision: a deploy token keeps working after the person who minted
    it is offboarded (even when that ends their account, as it does for
    Eli, whose only org this is), so CI does not break when its author
    leaves; the app's admins rotate or revoke it. It stops when the app
    or org it deploys to is gone."""
    app = _app(world.acme)
    _row, bearer = deploy_tokens.issue_token(app=app, name="ci", by_user_id=world.eli.pk)
    # Authenticated, but the deployment does not exist: a 404 from the view.
    assert _deploy_status(bearer).status_code == 404

    ending(world, app)

    assert (deploy_tokens.verify_token(bearer) is not None) is still_works
    assert _deploy_status(bearer).status_code == (404 if still_works else 401)


# ---- session-only relays: pinned, unchanged by #1910 -----------------


def _session_cookie(user) -> bytes:
    store = import_module(settings.SESSION_ENGINE).SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store.create()
    return f"sessionid={store.session_key}".encode()


def _vnc(task: AgentTask, headers, monkeypatch):
    return _websocket(
        vnc_ws_application,
        f"/app/vnc/{task.guid}",
        headers,
        monkeypatch,
        "core.schema.vnc_ws._resolve_vnc_task",
    )


def test_vnc_relay_never_accepts_a_bearer(world, monkeypatch):
    """The VNC relay authenticates by session cookie only, so an API token
    never opens it, whether or not its owner is still a member."""
    task = AgentTask.objects.create(organization=world.acme)
    bearer = [(b"authorization", f"Bearer {_token(world.dana, world.acme)}".encode())]

    assert _vnc(task, bearer, monkeypatch) == ([], _close(4401))


def test_vnc_session_stops_reaching_the_org_its_user_left(world, monkeypatch):
    """Pins the session path, which needed no change: the WebSocket relays
    infer the org from active ORG memberships only, so once Dana leaves
    Acme her session resolves to Globex and Acme's task is out of reach."""
    task = AgentTask.objects.create(organization=world.acme)
    cookie = [(b"cookie", _session_cookie(world.dana))]
    assert _vnc(task, cookie, monkeypatch) == ([world.acme.pk], _close(4410))

    _drop_membership(world.dana, world.acme)

    assert _vnc(task, cookie, monkeypatch) == ([], _close(4403))
