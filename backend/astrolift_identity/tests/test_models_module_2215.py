"""Models navigation mirrors live owner and legacy app read gates (#2215)."""

import json
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone

from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_identity.schema.queries import MeType
from core.permissions import Permission, module_entitlements
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db
READ = "query { me { modules { key enabled canView canCreate canManage canRun } } }"


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *_: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda *_: None))
    w = ScopeWorld("modelsmodule2215")
    w.user = make_user("modelsmodule2215")
    w.member = Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    return w


def grant(w, *, kind="ORG", permissions=(Permission.ORG_READ, Permission.CLUSTER_UPDATE)):
    owner = {"ORG": w.org, "TEAM": w.medops, "PROJECT": w.medops_project, "APP": w.medops_app}[kind]
    return bind_role(w.user, permissions=permissions, kind=kind, scope_id=owner.pk, slug="models")


@contextmanager
def caller(w, token=None):
    marker = set_current_api_token(token) if token is not None else None
    try:
        with tenant_context(
            TenantContext(organization_id=w.org.pk, team_id=w.medops.pk, actor_user_id=w.user.pk)
        ):
            yield
    finally:
        if marker is not None:
            reset_current_api_token(marker)


def read(w, token=None):
    with caller(w, token):
        return {row.key: row for row in MeType(id=str(w.user.pk), profile=None).modules(make_info(w.user))}


def capabilities(row):
    return row.can_view, row.can_create, row.can_manage, row.can_run


@pytest.mark.parametrize(
    "permissions,expected",
    [
        ((), (False, False, False, False)),
        (("org.read",), (True, False, False, False)),
        (("app.read",), (True, False, False, False)),
        (("agent.read",), (False, False, False, False)),
        (("cluster.update",), (False, False, False, True)),
    ],
)
def test_pure_mapping_has_dedicated_always_enabled_models_row(permissions, expected):
    rows = module_entitlements(permissions)
    assert capabilities(next(row for row in rows if row.key == "models")) == expected
    assert next(row for row in rows if row.key == "models").enabled
    assert [row.key for row in rows][:5] == ["apps", "agents", "workflows", "admin", "models"]
    assert capabilities(next(row for row in rows if row.key == "agents"))[0] == ("agent.read" in permissions)


def test_pure_superuser_has_models_capabilities():
    row = next(row for row in module_entitlements([], is_superuser=True) if row.key == "models")
    assert capabilities(row) == (True, True, True, True) and row.enabled


def test_org_reader_without_agent_read_sees_shared_models(world):
    grant(world, permissions=(Permission.ORG_READ,))
    rows = read(world)
    assert capabilities(rows["models"]) == (True, False, False, False)
    assert not rows["agents"].can_view


def test_org_owner_can_run_shared_prompt_but_cannot_host_or_configure(world):
    grant(world)
    assert capabilities(read(world)["models"]) == (True, False, False, True)


def test_scoped_superuser_bypass_preserves_actual_bearer_team_ceiling(world):
    world.user.is_superuser = True
    world.user.save()
    assert capabilities(read(world)["models"]) == (True, True, True, True)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        scopes=["admin"],
        name="operator team",
        token_hash=uuid4().hex,
    )
    assert capabilities(read(world, token)["models"]) == (True, False, False, False)
    ApiToken.objects.filter(pk=token.pk).update(scopes=["read:clusters"])
    assert capabilities(read(world, token)["models"]) == (False, False, False, False)


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM"])
def test_descendant_org_slugs_do_not_widen_into_owner_read_or_write(world, kind):
    grant(world, kind=kind)
    assert capabilities(read(world)["models"]) == (False, False, False, False)


def test_live_app_reader_sees_empty_legacy_model_catalogue_without_owner_write(world):
    grant(world, kind="APP", permissions=(Permission.APP_READ,))
    assert capabilities(read(world)["models"]) == (True, False, False, False)
    assert not read(world)["agents"].can_view


@pytest.mark.parametrize("retired", ["app", "project", "team"])
def test_retired_legacy_owner_does_not_keep_models_visible(world, retired):
    grant(world, kind="APP", permissions=(Permission.APP_READ,))
    assert read(world)["models"].can_view
    {"app": world.medops_app, "project": world.medops_project, "team": world.medops}[retired].soft_delete()
    assert not read(world)["models"].can_view


@pytest.mark.parametrize("change", ["user", "membership", "inactive-membership", "org", "grant"])
def test_current_account_membership_org_and_revoked_grant_are_rechecked(world, change):
    role = grant(world)
    assert read(world)["models"].can_view
    if change == "user":
        type(world.user).objects.filter(pk=world.user.pk).update(is_active=False)
    elif change == "membership":
        world.member.soft_delete()
    elif change == "inactive-membership":
        world.member.is_active = False
        world.member.save()
    elif change == "org":
        world.org.soft_delete()
    else:
        role.soft_delete()
    assert capabilities(read(world)["models"]) == (False, False, False, False)


@pytest.mark.parametrize("team", ["home", "sibling"])
def test_team_bearer_can_only_keep_actual_legacy_view_and_never_owner_write(world, team):
    grant(world)
    grant(world, kind="APP", permissions=(Permission.APP_READ,))
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops if team == "home" else world.platform,
        scopes=["admin"],
        name="team models",
        token_hash=uuid4().hex,
    )
    assert capabilities(read(world, token)["models"]) == (team == "home", False, False, False)


def test_direct_bearer_reloads_changed_scope_ceiling_instead_of_stale_token_object(world):
    grant(world)
    world.user.is_superuser = True
    world.user.save(update_fields=["is_superuser"])
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        scopes=["admin"],
        name="scope changes",
        token_hash=uuid4().hex,
    )
    assert capabilities(read(world, token)["models"]) == (True, True, True, True)
    ApiToken.objects.filter(pk=token.pk).update(scopes=["read:apps"])
    assert token.scopes == ["admin"]
    assert capabilities(read(world, token)["models"]) == (True, False, False, False)
    ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
    assert capabilities(read(world, token)["models"]) == (False, False, False, False)


@pytest.mark.parametrize("change", ["revoked", "expired", "foreign", "deleted"])
def test_invalid_direct_bearer_fails_closed_before_models_grants(world, change):
    grant(world)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        scopes=["admin"],
        name="invalid models",
        token_hash=uuid4().hex,
        is_revoked=change == "revoked",
        expires_at=timezone.now() - timedelta(seconds=1) if change == "expired" else None,
    )
    if change == "foreign":
        token.organization = ScopeWorld("modelsmoduleforeign").org
        token.save()
    elif change == "deleted":
        token.soft_delete()
    assert capabilities(read(world, token)["models"]) == (False, False, False, False)


@pytest.mark.parametrize("permission", [Permission.ORG_READ, Permission.CLUSTER_UPDATE])
def test_owner_policy_denials_are_reflected_without_flat_slug_fallback(world, permission):
    grant(world)
    Policy.objects.create(
        organization=world.org,
        name="Models owner denied",
        effect="DENY",
        action_pattern=permission.value,
        resource_pattern={},
        conditions=[],
    )
    expected = (
        (False, False, False, True) if permission == Permission.ORG_READ else (True, False, False, False)
    )
    assert capabilities(read(world)["models"]) == expected


@pytest.mark.parametrize(
    "scopes,team,expected",
    [
        (["read:apps"], None, (True, False, False, False)),
        (["admin"], None, (True, False, False, True)),
        (["admin"], "home", (True, False, False, False)),
        (["admin"], "sibling", (False, False, False, False)),
    ],
)
def test_actual_http_modules_apply_owner_and_bearer_scope_ceilings(world, scopes, team, expected):
    grant(world)
    grant(world, kind="APP", permissions=(Permission.APP_READ,))
    minted = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        scopes=scopes,
        team={"home": world.medops, "sibling": world.platform}.get(team),
        name="HTTP models",
        token_hash=minted.token_hash,
    )
    client = Client()

    def call():
        return client.post(
            "/app/gql/config/",
            data=json.dumps({"query": READ}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + minted.plaintext,
        )

    response = call()
    assert response.status_code == 200 and "errors" not in response.json()
    rows = {row["key"]: row for row in response.json()["data"]["me"]["modules"]}
    row = rows["models"]
    assert (row["canView"], row["canCreate"], row["canManage"], row["canRun"]) == expected
    assert row["enabled"] and not rows["agents"]["canView"]
    token.is_revoked = True
    token.save()
    assert call().status_code in {401, 403}
