"""NVKE-only ceilings, explicit device consent and live organization authority."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import Client
from django.utils import timezone

from astrolift_clusters.tests.test_nvke_sites import context, create
from astrolift_clusters.tests.test_nvke_sites import world as _world
from astrolift_identity import device_flow
from astrolift_identity.api_tokens import (
    ALLOWED_SCOPES,
    DEFAULT_SCOPES,
    SCOPE_CATALOG,
    mint_token,
    permissions_for_scopes,
    token_scope_allows_permission,
    token_scope_hint,
    token_scope_reaches,
)
from astrolift_identity.models import ApiToken, Organization, RoleBinding
from core.capabilities_registry import shipped_capabilities
from core.permissions import PermissionScope, ScopeKind

SCOPES = {"read:nvke": "nvke.read", "manage:nvke": "nvke.manage", "admin:nvke": "nvke.admin"}
PROFILES = {"cli-nvke-read": ["read:nvke"], "cli-nvke": list(SCOPES)}


@pytest.mark.parametrize(("scope", "permission"), SCOPES.items())
def test_each_scope_is_an_exact_permission_ceiling(scope, permission):
    assert permissions_for_scopes([scope]) == {permission}
    assert scope in ALLOWED_SCOPES and scope in {item.value for item in SCOPE_CATALOG}
    assert scope in token_scope_hint(permission)
    token = SimpleNamespace(scopes=[scope], organization_id=37)
    assert token_scope_reaches(token, permission, PermissionScope(ScopeKind.ORG, 37))
    for target in [None, PermissionScope(ScopeKind.ORG, 38), PermissionScope(ScopeKind.TEAM, 37)]:
        assert not token_scope_reaches(token, permission, target)


def test_nvke_administration_does_not_inherit_platform_or_future_permissions():
    token = SimpleNamespace(scopes=list(SCOPES))
    assert permissions_for_scopes(SCOPES) == set(SCOPES.values())
    for permission in [
        "api_token.create",
        "secret.read",
        "cluster.register",
        "org.update",
        "app.read",
        "nvke.future",
    ]:
        assert not token_scope_allows_permission(token, permission)
    assert "identity.nvke_scopes" in shipped_capabilities()
    for kind, scopes in PROFILES.items():
        assert device_flow.token_scopes_for_client_kind(kind.upper()) == scopes
    for kind in ["cli", "cli-operator", "cli-zentinelle", "mobile", "browser", "ide", "unknown"]:
        assert not set(SCOPES) & set(device_flow.token_scopes_for_client_kind(kind))


@pytest.fixture
def world():
    return _world.__wrapped__()


@pytest.mark.django_db
@pytest.mark.parametrize(("kind", "scopes"), PROFILES.items())
def test_browser_consent_issue_refresh_and_enrollment_keep_the_explicit_ceiling(world, kind, scopes):
    row, sid = device_flow.create_session(client_kind=kind)
    client = Client()
    client.force_login(world.user)
    page = client.get(f"/app/cli/auth/device/{sid}/").content.decode()
    for scope in scopes:
        assert f'<span class="scope">{scope}</span>' in page
    assert '<span class="scope">admin</span>' not in page
    assert ("NVKE administrative access." in page) == (kind == "cli-nvke")
    assert device_flow.approve_session(row, user=world.user, organization=world.org) is None
    now = timezone.now() + device_flow.MIN_POLL_INTERVAL + timedelta(seconds=1)
    issued = device_flow.poll_complete(sid, now=now)
    assert issued.status == "issued"
    row.refresh_from_db()
    assert row.api_token.scopes == scopes
    original = row.api_token_id
    refreshed = device_flow.refresh_credentials(
        issued.credentials.refresh_token, now=now + timedelta(seconds=5)
    )
    assert refreshed.status == "issued"
    row.refresh_from_db()
    assert row.api_token_id != original and row.api_token.scopes == scopes
    enrollment = device_flow.create_enrollment(user=world.user, organization=world.org)
    consumed = device_flow.consume_enrollment(enrollment.token_plaintext, client_kind=kind)
    assert consumed.status == "issued"
    assert consumed.session.api_token.scopes == list(DEFAULT_SCOPES)


def bearer(world, scopes):
    issued = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="NVKE automation",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=scopes,
    )
    return token, {
        "HTTP_AUTHORIZATION": f"Bearer {issued.plaintext}",
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid),
    }


def query(headers, text, variables=None):
    return Client().post(
        "/app/gql/config/",
        data={"query": text, "variables": variables or {}},
        content_type="application/json",
        **headers,
    )


@pytest.mark.django_db
def test_picker_and_minted_automation_token_keep_the_owners_role_intersection(world):
    role = RoleBinding.objects.get(user=world.user).role
    role.permissions = ["api_token.create", "nvke.read"]
    role.save()
    client = Client()
    client.force_login(world.user)
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    response = client.post(
        "/app/gql/config/",
        data={"query": "{astroliftApiTokenScopeCatalog{scopes{value available permissions}}}"},
        content_type="application/json",
        **headers,
    ).json()
    assert not response.get("errors")
    scopes = {item["value"]: item for item in response["data"]["astroliftApiTokenScopeCatalog"]["scopes"]}
    assert scopes["read:nvke"]["available"] is True
    assert scopes["manage:nvke"]["available"] is False
    assert scopes["admin:nvke"]["available"] is False
    mutation = """mutation($input:CreateApiTokenInput!){createApiToken(input:$input){ok data{plaintext apiToken{scopes effectivePermissions}}}}"""
    values = {"input": {"name": "NVKE reader automation", "scopes": list(SCOPES), "expiresInDays": 1}}
    created = client.post(
        "/app/gql/config/",
        data={"query": mutation, "variables": values},
        content_type="application/json",
        **headers,
    ).json()
    assert not created.get("errors") and created["data"]["createApiToken"]["ok"]
    data = created["data"]["createApiToken"]["data"]
    assert data["apiToken"]["scopes"] == list(SCOPES)
    assert data["apiToken"]["effectivePermissions"] == ["nvke.read"]
    headers["HTTP_AUTHORIZATION"] = "Bearer " + data["plaintext"]
    denied = query(headers, mutation, values).json()
    assert denied.get("errors") or denied["data"]["createApiToken"]["ok"] is False
    assert ApiToken.objects.filter(user=world.user).count() == 1


@pytest.mark.django_db
def test_narrow_bearer_reads_own_sites_and_org_discovery_but_cannot_administrate(world):
    with context(world):
        site = create()
    _, headers = bearer(world, ["read:nvke"])
    response = query(headers, "{astroliftOrganizations{id} astroliftNvkeSites(limit:25){items{id}}}")
    assert response.status_code == 200
    data = response.json()
    assert not data.get("errors")
    assert data["data"]["astroliftOrganizations"] == [{"id": str(world.org.guid)}]
    assert data["data"]["astroliftNvkeSites"]["items"] == [{"id": str(site.guid)}]
    refused = query(
        headers,
        "mutation($input:CreateNvkeSiteInput!){createNvkeSite(input:$input){ok errors{code}}}",
        {"input": {"name": "Not allowed", "slug": "not-allowed", "organizationNamespace": "blocked"}},
    )
    payload = refused.json()
    assert payload.get("errors") or payload["data"]["createNvkeSite"]["ok"] is False
    assert "api token scope" in str(payload).lower() or "PERMISSION_DENIED" in str(payload)


@pytest.mark.django_db
@pytest.mark.parametrize("authority", ["role", "membership", "revoked", "expired", "other_org"])
def test_narrow_scope_never_replaces_current_account_or_token_authority(world, authority):
    with context(world):
        create()
    token, headers = bearer(world, list(SCOPES))
    if authority == "role":
        RoleBinding.objects.filter(user=world.user).update(deleted_at=timezone.now())
    elif authority == "membership":
        world.member.soft_delete()
    elif authority == "revoked":
        token.is_revoked = True
        token.save()
    elif authority == "expired":
        token.expires_at = timezone.now() - timedelta(seconds=1)
        token.save()
    else:
        other = Organization.objects.create(name="Other NVKE org", slug="other-nvke-org")
        headers["HTTP_X_ASTROLIFT_ORGANIZATION"] = str(other.guid)
    response = query(headers, "{astroliftNvkeSites(limit:25){items{id}}}")
    assert response.status_code in {200, 401, 403}
    if response.status_code == 200:
        assert response.json().get("errors")
        assert not (response.json().get("data") or {}).get("astroliftNvkeSites")
