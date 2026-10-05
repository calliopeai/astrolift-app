"""Original authority captured through authenticated HTTP, including device refresh."""

from __future__ import annotations

import datetime as dt
import json
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_clusters.models import ManagedDomain
from astrolift_clusters.tests.domain_http_helpers_2287 import graphql_http, http_token
from astrolift_identity import device_flow
from astrolift_identity.models import ApiToken, DeviceFlowSession, Member
from astrolift_lifecycle import domain_route_authority as authority
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

pytestmark = pytest.mark.django_db(transaction=True)

SUPPORT = "query($id:GUID!,$version:Int!){managedDomainAppRouteSupport(domainId:$id,expectedDomainVersion:$version){domainId version allowed writer reason}}"


@pytest.fixture
def world(monkeypatch):
    w = ScopeWorld(uuid4().hex[:12])
    w.user = make_user(uuid4().hex[:12])
    Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk, is_active=True)
    bind_role(
        w.user,
        permissions=[
            Permission.PROVIDER_PLUGIN_READ,
            Permission.PROVIDER_PLUGIN_CONFIGURE,
            Permission.APP_UPDATE,
            Permission.CLUSTER_MANAGE,
        ],
        kind="ORG",
        scope_id=w.org.pk,
        slug=uuid4().hex,
    )
    w.domain = ManagedDomain.objects.create(
        organization=w.org, zone="example.test", dns_driver="route53", provision_zone_id="ZOWNED"
    )
    w.refs = []
    original = authority.capture

    def capture(request):
        result = original(request)
        w.refs.append(result)
        return result

    monkeypatch.setattr(authority, "capture", capture)
    w.token, w.headers = http_token(w, scopes=("admin",))
    return w


def read(client, w):
    return graphql_http(client, w.headers, SUPPORT, {"id": str(w.domain.guid), "version": w.domain.version})


def enter(w, ref=None):
    return authority.current(
        ref or w.refs[-1],
        permissions=((Permission.PROVIDER_PLUGIN_CONFIGURE, PermissionScope(ScopeKind.ORG, w.org.pk)),),
    )


def test_standalone_original_token_revocation_blocks_current_authority(world, client):
    assert not read(client, world).get("errors")
    with enter(world):
        pass
    ApiToken.objects.filter(pk=world.token.pk).update(is_revoked=True)
    with pytest.raises(PermissionDenied):
        with enter(world):
            pass
    replacement, _headers = http_token(world, scopes=("admin",))
    assert replacement.pk != world.token.pk
    with pytest.raises(PermissionDenied):
        with enter(world):
            pass


def device(w):
    row = DeviceFlowSession.objects.create(
        session_guid=uuid4().hex,
        approved_user=w.user,
        organization=w.org,
        expires_at=timezone.now() + dt.timedelta(minutes=10),
        approved_at=timezone.now(),
        state=DeviceFlowSession.STATE_APPROVED,
        client_kind="cli-operator",
    )
    with __import__("django.db", fromlist=["transaction"]).transaction.atomic():
        row = DeviceFlowSession.objects.select_for_update().get(pk=row.pk)
        credentials = device_flow._consume_session_locked(row, now=timezone.now())
    w.headers["HTTP_AUTHORIZATION"] = "Bearer " + credentials.access_token
    return row, credentials


def test_original_device_session_refresh_preserves_only_accepted_scope_ceiling(world, client):
    row, credentials = device(world)
    # This scope is the initial authenticated ceiling; the server's ordinary
    # operator refresh mints a different narrower ceiling, never a new grant.
    ApiToken.objects.filter(pk=row.api_token_id).update(scopes=["admin"])
    assert not read(client, world).get("errors")
    original = world.refs[-1]
    with enter(world, original):
        pass
    reply = client.post(
        "/api/cli/v1/auth/refresh",
        json.dumps({"refresh_token": credentials.refresh_token}),
        content_type="application/json",
    )
    assert reply.status_code == 200
    row.refresh_from_db()
    assert str(row.guid) == original["payload"]["credential"]
    # Actual refresh drops admin. Current bearer still has cluster.manage,
    # but cannot lend a provider.configure grant from the older ceiling.
    with pytest.raises(PermissionDenied):
        with enter(world, original):
            pass
    with authority.current(
        original, permissions=((Permission.CLUSTER_MANAGE, PermissionScope(ScopeKind.ORG, world.org.pk)),)
    ):
        pass
    ended = client.post(
        "/api/cli/v1/auth/signout",
        json.dumps({"refresh_token": reply.json()["refresh_token"]}),
        content_type="application/json",
    )
    assert ended.status_code == 200
    with pytest.raises(PermissionDenied):
        with authority.current(
            original, permissions=((Permission.CLUSTER_MANAGE, PermissionScope(ScopeKind.ORG, world.org.pk)),)
        ):
            pass


def test_device_replacement_with_broader_scopes_cannot_widen_initial_ceiling(world, client):
    row, credentials = device(world)
    assert not read(client, world).get("errors")
    original = world.refs[-1]
    assert "admin" not in original["payload"]["scopes"]
    with pytest.raises(PermissionDenied):
        with enter(world, original):
            pass
    replacement, _headers = http_token(world, scopes=("admin",))
    DeviceFlowSession.objects.filter(pk=row.pk).update(api_token=replacement)
    with pytest.raises(PermissionDenied):
        with enter(world, original):
            pass


@pytest.mark.parametrize("withdrawal", ["member", "actor", "organization", "team", "source_hash"])
def test_original_token_fresh_withdrawals_refuse(world, client, withdrawal):
    assert not read(client, world).get("errors")
    if withdrawal == "member":
        Member.objects.filter(user=world.user, scope_kind="ORG", scope_id=world.org.pk).update(
            is_active=False
        )
    elif withdrawal == "actor":
        type(world.user).objects.filter(pk=world.user.pk).update(is_active=False)
    elif withdrawal == "organization":
        type(world.org).all_objects.filter(pk=world.org.pk).update(deleted_at=timezone.now())
    elif withdrawal == "team":
        ApiToken.objects.filter(pk=world.token.pk).update(team=world.medops)
    else:
        ApiToken.objects.filter(pk=world.token.pk).update(token_hash="different")
    with pytest.raises(PermissionDenied):
        with enter(world):
            pass
