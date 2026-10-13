"""Brain read tokens (#2320): issued only for a brain the caller can read,
verifiable against the published JWKS, scoped to that brain, short-lived, and
recorded for the edge only for brain apps."""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import jwt
import pytest
from django.test import Client
from django.utils import timezone

from astrolift_agents.views.mcp import McpCallError, _connect_brain
from astrolift_registry import brain_tokens
from astrolift_registry.models import AppTeamAccess, BrainTokenKey
from astrolift_registry.schema.mutations import RegistryMutation
from core import edge_access
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user
from providers.k8s_native.edge_gateway import BRAIN_TOKEN_HEADER

pytestmark = pytest.mark.django_db

ISSUER = "https://astrolift.example.net"


@pytest.fixture(autouse=True)
def _env(monkeypatch, settings):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))
    settings.APP_BASE_URL = ISSUER


@pytest.fixture
def world():
    from astrolift_registry.models import RegisteredApp

    world = ScopeWorld("tok2320")
    world.user = make_user("tok2320")
    RegisteredApp.objects.filter(pk__in=[world.medops_app.pk, world.platform_app.pk]).update(
        app_kind="mimwell"
    )
    world.medops_app.refresh_from_db()
    world.platform_app.refresh_from_db()
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="TEAM", scope_id=world.medops.pk, slug="reader"
    )
    return world


def _as(world):
    return tenant_context(
        TenantContext(organization_id=world.org.pk, team_id=world.medops.pk, actor_user_id=world.user.pk)
    )


def _issue(world, slug):
    with _as(world):
        return RegistryMutation().issue_brain_read_token(make_info(world.user), slug=slug)


def _verify(token, audience):
    keys = {k["kid"]: k for k in brain_tokens.jwks()["keys"]}
    kid = jwt.get_unverified_header(token)["kid"]
    public = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(keys[kid]))
    return jwt.decode(token, public, algorithms=["ES256"], audience=audience, issuer=ISSUER)


def test_a_readable_brain_gets_a_token_the_published_keys_verify(world):
    result = _issue(world, world.medops_app.slug)

    assert result.ok, result.errors
    data = result.data
    assert data.header == BRAIN_TOKEN_HEADER
    claims = _verify(data.token, str(world.medops_app.guid))
    assert claims["scope"] == "brain:read"
    assert claims["email"] == world.user.email
    assert claims["exp"] - claims["iat"] == 15 * 60


def test_an_unshared_brain_a_generic_app_and_a_revoked_share_get_no_token(world):
    assert _issue(world, world.platform_app.slug).errors[0].code == "NOT_FOUND"

    share = AppTeamAccess.objects.create(
        registered_app=world.platform_app, team=world.medops, access_level=AppTeamAccess.AccessLevel.VIEWER
    )
    assert _issue(world, world.platform_app.slug).ok
    share.deleted_at = timezone.now()
    share.save(update_fields=["deleted_at"])
    assert _issue(world, world.platform_app.slug).errors[0].code == "NOT_FOUND"

    from astrolift_registry.models import RegisteredApp

    RegisteredApp.objects.filter(pk=world.medops_app.pk).update(app_kind="")
    assert _issue(world, world.medops_app.slug).errors[0].code == "NOT_FOUND"


def test_a_token_for_one_brain_does_not_verify_for_another(world):
    token = _issue(world, world.medops_app.slug).data.token
    with pytest.raises(jwt.InvalidAudienceError):
        _verify(token, str(world.platform_app.guid))


def test_keys_rotate_and_a_retired_key_stays_published_for_a_day():
    now = timezone.now()
    first = brain_tokens.signing_key(now=now)
    assert brain_tokens.signing_key(now=now + dt.timedelta(days=1)).kid == first.kid

    later = now + dt.timedelta(days=31)
    second = brain_tokens.signing_key(now=later)
    assert second.kid != first.kid
    published = {k["kid"] for k in brain_tokens.jwks(now=later + dt.timedelta(hours=1))["keys"]}
    assert published == {first.kid, second.kid}
    published = {k["kid"] for k in brain_tokens.jwks(now=later + dt.timedelta(days=2))["keys"]}
    assert published == {second.kid}
    assert all("d" not in k for k in brain_tokens.jwks(now=later)["keys"]), "a private key was published"


def test_the_jwks_is_public_and_never_empty():
    response = Client().get("/api/brains/v1/jwks.json")
    assert response.status_code == 200
    keys = response.json()["keys"]
    assert keys and keys[0]["alg"] == "ES256" and "d" not in keys[0]
    assert BrainTokenKey.objects.count() == 1


def test_mcp_connect_brain_returns_the_descriptor_or_not_found(world):
    request = SimpleNamespace(user=world.user)
    with _as(world):
        row = _connect_brain(request, {"slug": world.medops_app.slug})
        assert row["header"] == BRAIN_TOKEN_HEADER and row["token"]
        with pytest.raises(McpCallError):
            _connect_brain(request, {"slug": world.platform_app.slug})


def test_the_edge_rule_carries_the_audience_for_brain_apps_only(world):
    assert edge_access.brain_fields(world.medops_app) == {
        "brain_audience": str(world.medops_app.guid),
        "brain_issuer": ISSUER,
        "brain_jwks_uri": ISSUER + "/api/brains/v1/jwks.json",
    }
    world.medops_app.app_kind = ""
    assert edge_access.brain_fields(world.medops_app) == {}
