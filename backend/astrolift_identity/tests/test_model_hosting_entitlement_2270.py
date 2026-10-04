"""Current hosting hints distinguish operator writes from ordinary prompt authority."""

import json
from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.utils import timezone

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, AstroliftSession
from astrolift_identity.tests.test_models_module_2215 import READ
from astrolift_identity.tests.test_models_module_2215 import world as source_world
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    return source_world.__wrapped__(monkeypatch)


def all_grants(w, *, operator=False):
    w.user.is_superuser = operator
    w.user.save(update_fields=["is_superuser"])
    bind_role(
        w.user,
        permissions=[Permission.ORG_READ, Permission.ORG_UPDATE, Permission.CLUSTER_UPDATE],
        kind="ORG",
        scope_id=w.org.pk,
        slug="hosting-entitlement-org",
    )
    bind_role(
        w.user,
        permissions=[Permission.APP_READ, Permission.APP_UPDATE],
        kind="APP",
        scope_id=w.medops_app.pk,
        slug="hosting-entitlement-app",
    )


def post(client, headers, query=READ):
    response = client.post(
        "/app/gql/config/",
        data=json.dumps({"query": query}),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.status_code
    payload = response.json()
    assert not payload.get("errors"), payload
    return payload


def model_row(client, headers):
    rows = post(client, headers)["data"]["me"]["modules"]
    return next(row for row in rows if row["key"] == "models")


@pytest.mark.parametrize("operator", [False, True])
@pytest.mark.parametrize("credential", ["org_admin", "team_admin", "read_apps", "cluster_write"])
def test_actual_http_hosting_manifest_matches_current_operator_and_bearer_ceiling(
    world, client, operator, credential
):
    all_grants(world, operator=operator)
    scopes = {
        "org_admin": ["admin"],
        "team_admin": ["admin"],
        "read_apps": ["read:apps"],
        "cluster_write": ["read:apps", "write:clusters"],
    }[credential]
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops if credential == "team_admin" else None,
        scopes=scopes,
        name="manifest",
        token_hash=minted.token_hash,
    )
    row = model_row(client, {"HTTP_AUTHORIZATION": "Bearer " + minted.plaintext})
    assert row["enabled"] and row["canView"]
    expected_hosting = operator and credential == "org_admin"
    assert row["canCreate"] == expected_hosting and row["canManage"] == expected_hosting
    # Shared prompts retain their ordinary exact ORG cluster-update permission;
    # app prompts/subscription mutations retain their separate APP_UPDATE gates.
    assert row["canRun"] == (credential in {"org_admin", "cluster_write"})


@pytest.mark.parametrize("operator", [False, True])
def test_actual_authenticated_browser_hosting_manifest_retains_ordinary_prompt_permission(
    world, client, operator
):
    all_grants(world, operator=operator)
    client.force_login(world.user)
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    row = model_row(client, headers)
    assert row["canView"] and row["canRun"]
    assert row["canCreate"] == operator and row["canManage"] == operator


@pytest.mark.parametrize(
    "withdrawal", ["logout", "sidecar_revoked", "sidecar_expired", "auth_hash", "operator"]
)
def test_actual_http_hosting_hints_recheck_current_auth_after_initial_request_admission(
    world, client, monkeypatch, withdrawal
):
    import astrolift_services.schema.hf_connections as host_api

    all_grants(world, operator=True)
    client.force_login(world.user)
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    post(client, headers, "query{__typename}")
    key = client.session.session_key
    sidecar = AstroliftSession.objects.get(session_key=key)
    original = host_api.require_host_admin
    observed = []

    def withdraw_then_check(info, cluster=None):
        assert info.context.request.user.is_authenticated
        assert info.context.request.user.is_superuser
        if not observed:
            if withdrawal == "logout":
                Session.objects.filter(session_key=key).delete()
            elif withdrawal == "sidecar_revoked":
                AstroliftSession.all_objects.filter(pk=sidecar.pk).update(revoked_at=timezone.now())
            elif withdrawal == "sidecar_expired":
                AstroliftSession.all_objects.filter(pk=sidecar.pk).update(
                    expires_at=timezone.now() - timedelta(seconds=1)
                )
            elif withdrawal == "auth_hash":
                world.user.set_password("synthetic-new-password")
                world.user.save(update_fields=["password"])
            else:
                type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False)
            observed.append(
                AstroliftSession.all_objects.filter(pk=sidecar.pk)
                .values("revoked_at", "expires_at", "deleted_at")
                .get()
            )
        return original(info, cluster)

    monkeypatch.setattr(host_api, "require_host_admin", withdraw_then_check)
    row = model_row(client, headers)
    assert observed
    assert not row["canCreate"] and not row["canManage"]
    if withdrawal in {"sidecar_revoked", "sidecar_expired"}:
        assert (
            AstroliftSession.all_objects.filter(pk=sidecar.pk)
            .values("revoked_at", "expires_at", "deleted_at")
            .get()
            == observed[0]
        )
    if withdrawal == "logout":
        assert not Session.objects.filter(session_key=key).exists()
