# ruff: noqa: F811
"""Real role/bearer owner checks precede the configuration snapshot."""

import json
from types import SimpleNamespace

import pytest
from constance.test import override_config
from django.test import Client

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_lifecycle.models import DeployToken
from astrolift_lifecycle.schema.mutations import LifecycleMutation, RotateDeployTokenInput
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.tests.test_owner_scopes_2104 import no_search, subject, world  # noqa: F401
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import ScopeWorld, bind_role

pytestmark = pytest.mark.django_db
QUERY = (
    "query($slug:String!){ astroliftAppDeployTokenRotationMetadata(appSlug:$slug){ rotationGraceSeconds } }"
)


def info(w):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=w.user)))


def read(w, slug=None):
    return LifecycleQuery().astrolift_app_deploy_token_rotation_metadata(info(w), slug or w.medops_app.slug)


def grant(w, kind="ORG", permissions=None, owner=None):
    owner = owner or {"APP": w.medops_app, "TEAM": w.medops, "PROJECT": w.medops_project, "ORG": w.org}[kind]
    return bind_role(
        w.user,
        permissions=permissions or [Permission.APP_UPDATE],
        kind=kind,
        scope_id=owner.pk,
        slug="metadata",
    )


@pytest.mark.parametrize("kind", ["APP", "TEAM", "PROJECT", "ORG"])
def test_metadata_uses_actual_owner_update_grant_not_selected_sibling(world, kind):
    grant(world, kind)
    with subject(world), override_config(DEPLOY_TOKEN_ROTATION_GRACE_SECONDS=10800):
        assert read(world).rotation_grace_seconds == 10800
        if kind != "ORG":
            with pytest.raises(PermissionDenied):
                read(world, world.platform_app.slug)


@pytest.mark.parametrize("kind", ["APP", "TEAM", "PROJECT"])
def test_actual_owner_deny_refuses_configuration_before_read(world, kind, monkeypatch):
    grant(world)
    owner = {"APP": world.medops_app, "TEAM": world.medops, "PROJECT": world.medops_project}[kind]
    Policy.objects.create(
        organization=world.org,
        name="No token updates",
        slug="no-token-updates",
        effect="DENY",
        scope_level=kind,
        scope_id=owner.pk,
        action_pattern="app.update",
    )
    reads = []
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.queries.rotation_grace_seconds_from_constance", lambda: reads.append(True)
    )
    with subject(world), pytest.raises(PermissionDenied):
        read(world)
    assert reads == []


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "deleted-app",
        "deleted-project",
        "deleted-team",
        "foreign-project",
        "incoherent-team",
        "foreign-app",
    ],
)
def test_invalid_live_owner_never_reads_configuration_even_with_org_grant(world, invalid, monkeypatch):
    grant(world)
    slug = world.medops_app.slug
    if invalid == "missing":
        slug = "absent"
    elif invalid == "deleted-app":
        world.medops_app.soft_delete()
    elif invalid == "deleted-project":
        world.medops_project.soft_delete()
    elif invalid == "deleted-team":
        world.medops.soft_delete()
    elif invalid == "foreign-project":
        world.medops_app.project = ScopeWorld("foreign-metadata").medops_project
        world.medops_app.save()
    elif invalid == "incoherent-team":
        world.medops_app.team = world.platform
        world.medops_app.save()
    else:
        slug = ScopeWorld("foreign-metadata").medops_app.slug
    reads = []
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.queries.rotation_grace_seconds_from_constance", lambda: reads.append(True)
    )
    with subject(world):
        assert read(world, slug) is None
    assert reads == []


@pytest.mark.parametrize(
    "raw,expected", [(10800, 10800), (90, 90), (0, 60), (-50, 60), (604801, 604800), ("invalid", 86400)]
)
def test_metadata_and_actual_rotation_share_validation_and_persisted_window(world, raw, expected):
    grant(world, "APP")
    row = world.rows["medops"]["deploy_token"]
    with subject(world), override_config(DEPLOY_TOKEN_ROTATION_GRACE_SECONDS=raw):
        assert read(world).rotation_grace_seconds == expected
        result = LifecycleMutation().rotate_deploy_token(
            info(world), input=RotateDeployTokenInput(id=str(row.guid))
        )
    assert result.ok, result.errors
    assert result.data.rotation_grace_seconds == expected
    row.refresh_from_db()
    assert (row.previous_token_expires_at - row.last_rotated_at).total_seconds() == expected


@pytest.mark.parametrize(
    "authority",
    [
        "read-only",
        "app-deploy-only",
        "sibling-role",
        "team-ceiling",
        "read-ceiling",
        "owner",
        "inactive-user",
        "deleted-member",
        "revoked-token",
        "deleted-org",
        "foreign-org-bearer",
    ],
)
def test_http_real_role_and_bearer_gate_and_recheck_revocation(world, authority, monkeypatch):
    member = Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    perms = (
        [Permission.APP_READ]
        if authority == "read-only"
        else [Permission.APP_DEPLOY]
        if authority == "app-deploy-only"
        else [Permission.APP_UPDATE]
    )
    kind = "APP" if authority in ("owner", "sibling-role") else "ORG"
    owner = (
        world.platform_app
        if authority == "sibling-role"
        else world.medops_app
        if kind == "APP"
        else world.org
    )
    binding = grant(world, kind, perms, owner)
    minted = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="metadata-http",
        token_hash=minted.token_hash,
        scopes=["read:apps"] if authority == "read-ceiling" else ["admin"],
        team=world.platform if authority == "team-ceiling" else None,
    )
    if authority == "inactive-user":
        world.user.is_active = False
        world.user.save()
    if authority == "deleted-member":
        member.soft_delete()
    if authority == "revoked-token":
        token.is_revoked = True
        token.save()
    if authority == "deleted-org":
        world.org.soft_delete()
    if authority == "foreign-org-bearer":
        foreign = ScopeWorld("foreign-http-metadata")
        Member.objects.create(user=world.user, scope_kind="ORG", scope_id=foreign.org.pk)
        token.organization = foreign.org
        token.save()
    reads = []
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.queries.rotation_grace_seconds_from_constance",
        lambda: reads.append(True) or 10800,
    )
    client = Client()

    def send():
        return client.post(
            "/app/gql/config/",
            data=json.dumps({"query": QUERY, "variables": {"slug": world.medops_app.slug}}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + minted.plaintext,
            HTTP_X_ASTROLIFT_TEAM=str(world.platform.pk),
            HTTP_X_ASTROLIFT_PROJECT=str(world.platform_project.pk),
        )

    before = list(DeployToken.objects.values())
    response = send()
    if authority == "owner":
        assert response.status_code == 200, response.content
        assert (
            response.json()["data"]["astroliftAppDeployTokenRotationMetadata"]["rotationGraceSeconds"]
            == 10800
        )
        binding.soft_delete()
        reads.clear()
        response = send()
    assert response.status_code in (200, 401, 403), response.content
    if response.status_code == 200:
        result = response.json()
        assert result.get("errors"), result
    assert reads == []
    assert list(DeployToken.objects.values()) == before
