"""Hosted brain discovery follows app visibility exactly (#2320)."""

from contextlib import contextmanager

import pytest
from django.utils import timezone

from astrolift_agents.views.mcp import McpCallError, _get_brain, _list_brains
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.brains import describe_brain, visible_brains
from astrolift_registry.models import AppTeamAccess, RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))


@pytest.fixture
def world():
    world = ScopeWorld("brains2320")
    world.user = make_user("brains2320")
    RegisteredApp.objects.filter(pk__in=[world.medops_app.pk, world.platform_app.pk]).update(
        app_kind="mimwell"
    )
    world.medops_app.refresh_from_db()
    world.platform_app.refresh_from_db()
    # A generic app the reader can see is never a brain.
    world.plain = RegisteredApp.objects.create(
        organization=world.org,
        team=world.medops,
        project=world.medops_project,
        name="Plain",
        slug="plain-brains2320",
        k8s_namespace="acme-plain-brains2320",
        provisioning_status="ready",
    )
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="TEAM", scope_id=world.medops.pk, slug="reader"
    )
    return world


@contextmanager
def _as(world, *, token_team=None):
    with tenant_context(
        TenantContext(organization_id=world.org.pk, team_id=world.medops.pk, actor_user_id=world.user.pk)
    ):
        handle = None
        if token_team is not None:
            handle = set_current_api_token(
                ApiToken.objects.create(
                    user=world.user,
                    name="brains",
                    token_hash="hash-brains2320",
                    organization_id=world.org.pk,
                    team_id=token_team,
                    scopes=["admin"],
                )
            )
        try:
            yield
        finally:
            if handle is not None:
                reset_current_api_token(handle)


def _slugs():
    return sorted(app.slug for app in visible_brains())


def test_a_team_reader_sees_its_brains_then_a_shared_one_until_the_share_is_revoked(world):
    with _as(world):
        assert _slugs() == [world.medops_app.slug]
    share = AppTeamAccess.objects.create(
        registered_app=world.platform_app, team=world.medops, access_level=AppTeamAccess.AccessLevel.VIEWER
    )
    with _as(world):
        assert _slugs() == sorted([world.medops_app.slug, world.platform_app.slug])
    share.deleted_at = timezone.now()
    share.save(update_fields=["deleted_at"])
    with _as(world):
        assert _slugs() == [world.medops_app.slug]


def test_a_team_token_is_bounded_by_its_own_team(world):
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="ORG", scope_id=world.org.pk, slug="org-reader"
    )
    with _as(world):
        assert _slugs() == sorted([world.medops_app.slug, world.platform_app.slug])
    with _as(world, token_team=world.platform.pk):
        assert _slugs() == [world.platform_app.slug]


def test_the_descriptor_lists_endpoints_and_live_shares_without_credentials(world):
    cluster = make_cluster(world, "brains2320")
    AppEnvironment.objects.create(
        registered_app=world.medops_app,
        tenant_cluster=cluster,
        name="production",
        url="https://brain.acme.example",
    )
    AppEnvironment.objects.create(registered_app=world.medops_app, tenant_cluster=cluster, name="draft")
    AppTeamAccess.objects.create(
        registered_app=world.medops_app, team=world.platform, access_level=AppTeamAccess.AccessLevel.VIEWER
    )
    row = describe_brain(world.medops_app)
    assert row["kind"] == "mimwell" and row["team"] == world.medops.slug
    assert row["environments"] == [{"name": "production", "url": "https://brain.acme.example"}]
    assert row["shared_with"] == [{"team": world.platform.slug, "access": "viewer"}]
    assert not {"token", "secret", "password", "credential"} & set(row)


def test_mcp_and_graphql_answer_the_same_question(world):
    with _as(world):
        listed = _list_brains(None, {})
        assert [b["slug"] for b in listed["brains"]] == [world.medops_app.slug]
        assert _get_brain(None, {"slug": world.medops_app.slug})["id"] == str(world.medops_app.guid)
        # Invisible, generic and missing all read the same: not found.
        for slug in (world.platform_app.slug, world.plain.slug, "nope", ""):
            with pytest.raises(McpCallError) as exc:
                _get_brain(None, {"slug": slug})
            assert exc.value.code == "not_found"
        graph = RegistryQuery().astrolift_brains(make_info(world.user))
        assert [b.slug for b in graph] == [world.medops_app.slug]
