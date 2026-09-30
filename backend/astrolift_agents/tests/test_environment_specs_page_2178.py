"""The org recipe home counts and pages only the caller's live owners."""

import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone
from graphql import GraphQLError

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.schema.types import AgentEnvironmentSpecsFilterInput
from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )


@pytest.fixture
def world():
    w = ScopeWorld("2178")
    w.user = make_user("2178")
    w.info = make_info(w.user)
    w.other = Organization.objects.create(name="Other", slug="other2178")
    w.specs = {}
    for slug, owner in [
        ("shared", {}),
        ("medops", {"team": w.medops}),
        ("project", {"project": w.medops_project, "team": w.medops}),
        ("platform", {"project": w.platform_project, "team": w.platform}),
    ]:
        w.specs[slug] = AgentEnvironmentSpec.objects.create(
            organization=w.org, name=slug.title(), slug=slug, agent_type="claude", runtime="claude", **owner
        )
    AgentEnvironmentSpec.objects.create(
        organization=w.other, name="Foreign", slug="foreign", agent_type="codex"
    )
    return w


def bind(w, kind="TEAM"):
    target = {"TEAM": w.medops, "PROJECT": w.medops_project, "ORG": w.org}[kind]
    return bind_role(
        w.user,
        permissions=[Permission.AGENT_ENV_SPEC_READ],
        kind=kind,
        scope_id=target.pk,
        slug="specs" + uuid4().hex,
    )


def context(w):
    return tenant_context(
        TenantContext(organization_id=w.org.pk, actor_user_id=w.user.pk, team_id=w.platform.pk)
    )


def page(w, **kwargs):
    with context(w):
        return AgentsQuery().agent_environment_specs_page(w.info, org_id=str(w.org.guid), **kwargs)


@contextmanager
def token(w, *, org=None, team=None, scopes=("admin",)):
    credential = ApiToken.objects.create(
        user=w.user,
        organization=org or w.org,
        team=team,
        name="specs",
        token_hash=uuid4().hex * 2,
        scopes=list(scopes),
    )
    marker = set_current_api_token(credential)
    try:
        yield credential
    finally:
        reset_current_api_token(marker)


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("TEAM", {"shared", "medops", "project"}),
        ("PROJECT", {"shared", "project"}),
        ("ORG", {"shared", "medops", "project", "platform"}),
    ],
)
def test_counts_and_detail_agree_on_actual_owner(world, kind, expected):
    bind(world, kind)
    result = page(world, page_size=1)
    assert result.total_count == len(expected)
    seen = set()
    for number in range(1, len(expected) + 1):
        for item in page(world, page=number, page_size=1).items:
            seen.add(item.slug)
            with context(world):
                assert AgentsQuery().agent_environment_spec(world.info, slug=item.slug).id == item.id
    assert seen == expected
    with context(world):
        assert AgentsQuery().agent_environment_spec(world.info, slug="foreign") is None


def test_beyond_legacy_cap_search_sort_and_mine(world):
    bind(world, "ORG")
    AgentEnvironmentSpec.objects.bulk_create(
        [
            AgentEnvironmentSpec(
                organization=world.org,
                name=f"Recipe {n:03}",
                slug=f"recipe-{n:03}",
                agent_type="codex",
                runtime="aider",
                created_by=world.user if n == 204 else None,
            )
            for n in range(205)
        ]
    )
    result = page(world, search="recipe", sort="-name", page=3, page_size=100)
    assert result.total_count == 205 and len(result.items) == 5
    assert [r.slug for r in result.items] == [f"recipe-{n:03}" for n in range(4, -1, -1)]
    result = page(
        world,
        search="204",
        filter=AgentEnvironmentSpecsFilterInput(agent_type=["codex"], runtime=["aider"], created_by=["me"]),
    )
    assert result.total_count == 1 and result.items[0].slug == "recipe-204"
    assert page(world, filter=AgentEnvironmentSpecsFilterInput(agent_type=["missing"])).total_count == 0


def test_denies_foreign_org_and_missing_permission(world):
    with pytest.raises(PermissionDenied):
        page(world)
    bind(world)
    with context(world), pytest.raises(GraphQLError):
        AgentsQuery().agent_environment_specs_page(world.info, org_id=str(world.other.guid))
    with context(world), pytest.raises(GraphQLError):
        AgentsQuery().agent_environment_spec(world.info, slug="foreign", org_id=str(world.other.guid))


@pytest.mark.parametrize(
    "ceiling,expected",
    [("own-team", {"shared", "medops", "project"}), ("foreign-org", set()), ("no-read", set())],
)
def test_bearer_ceiling_narrows_counts_and_pages(world, ceiling, expected):
    bind(world, "ORG")
    with token(
        world,
        org=world.other if ceiling == "foreign-org" else None,
        team=world.medops if ceiling == "own-team" else None,
        scopes=("mcp:dispatch",) if ceiling == "no-read" else ("admin",),
    ):
        if ceiling == "no-read":
            with pytest.raises(PermissionDenied):
                page(world)
        else:
            result = page(world)
            assert result.total_count == len(expected)
            assert {r.slug for r in result.items} == expected


def test_revoked_binding_deleted_owner_and_recipe_not_reclassified(world):
    binding = bind(world)
    world.medops_project.deleted_at = timezone.now()
    world.medops_project.save(update_fields=["deleted_at"])
    world.specs["medops"].deleted_at = timezone.now()
    world.specs["medops"].save(update_fields=["deleted_at"])
    assert {r.slug for r in page(world).items} == {"shared"}
    binding.deleted_at = timezone.now()
    binding.save(update_fields=["deleted_at"])
    with pytest.raises(PermissionDenied):
        page(world)


def test_http_team_bearer_filters_and_foreign_detail_stays_null(world):
    bind(world, "ORG")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="http",
        token_hash=minted.token_hash,
        scopes=["admin"],
    )
    query = 'query($org:ID!){ agentEnvironmentSpecsPage(orgId:$org,pageSize:1){ totalCount page pageSize items{slug} } agentEnvironmentSpec(slug:"platform"){slug} }'
    response = Client().post(
        "/app/gql/config/",
        data=json.dumps({"query": query, "variables": {"org": str(world.org.guid)}}),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + minted.plaintext,
        HTTP_X_ASTROLIFT_TEAM=str(world.platform.pk),
    )
    assert response.status_code == 200
    result = response.json()
    assert not result.get("errors"), result
    assert result["data"]["agentEnvironmentSpecsPage"]["totalCount"] == 3
    assert result["data"]["agentEnvironmentSpec"] is None
