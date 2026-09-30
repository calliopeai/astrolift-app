"""Organization pipeline creation cannot borrow selected team or app grants."""

from contextlib import contextmanager

import pytest
from django.conf import settings
from django.test import Client

from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member
from astrolift_pipelines.models import Pipeline
from astrolift_pipelines.schema.mutations import CreatePipelineInput, PipelinesMutation
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    world = ScopeWorld("pipeline2115")
    world.user = make_user("pipeline2115")
    world.foreign = ScopeWorld("pipeline2115-foreign")
    return world


def grant(world, kind="ORG", *, foreign=False):
    owner = world.foreign if foreign else world
    row = {"ORG": owner.org, "TEAM": owner.medops, "PROJECT": owner.medops_project, "APP": owner.medops_app}[
        kind
    ]
    bind_role(
        world.user, permissions=[Permission.APP_UPDATE], kind=kind, scope_id=row.pk, slug="pipeline-create"
    )


def selected(world, *, sibling=False):
    return tenant_context(
        TenantContext(
            organization_id=world.org.pk,
            actor_user_id=world.user.pk,
            team_id=(world.platform if sibling else world.medops).pk,
            project_id=(world.platform_project if sibling else world.medops_project).pk,
        )
    )


def create(world):
    return PipelinesMutation().create_pipeline(
        make_info(world.user),
        input=CreatePipelineInput(name="build", repo_url=" https://github.com/acme/build "),
    )


@contextmanager
def credential(world, *, team=None, foreign=False, scopes=("write:apps",)):
    issued = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.foreign.org if foreign else world.org,
        team=team,
        name="pipeline create",
        token_hash=issued.token_hash,
        scopes=list(scopes),
    )
    handle = set_current_api_token(token)
    try:
        yield issued
    finally:
        reset_current_api_token(handle)


@pytest.mark.parametrize("kind,foreign", (("APP", False), ("PROJECT", False), ("TEAM", False), ("ORG", True)))
@pytest.mark.parametrize("sibling", (False, True))
def test_scoped_and_foreign_grants_cannot_create_an_org_pipeline(world, kind, foreign, sibling):
    grant(world, kind, foreign=foreign)
    with selected(world, sibling=sibling), pytest.raises(PermissionDenied):
        create(world)
    assert not Pipeline.objects.exists()


@pytest.mark.parametrize("sibling", (False, True))
def test_org_grant_creates_existing_unassociated_pipeline_contract(world, sibling):
    grant(world)
    with selected(world, sibling=sibling):
        result = create(world)
    assert result.ok
    row = Pipeline.objects.get()
    assert row.organization_id == world.org.pk
    assert row.registered_app_id is None
    assert row.repo_url == "https://github.com/acme/build"
    assert row.default_branch == "main"
    assert row.toml_path == ".astrolift/pipelines/build.toml"


@pytest.mark.parametrize("case", ("team", "operator-team", "foreign", "read", "write", "admin"))
def test_bearer_cannot_widen_org_creation_authority(world, case):
    grant(world)
    if case == "operator-team":
        world.user.is_superuser = True
        world.user.save(update_fields=["is_superuser"])
    team = world.medops if case in ("team", "operator-team") else None
    scopes = (
        ("read:apps",)
        if case == "read"
        else ("admin",)
        if case in ("operator-team", "admin")
        else ("write:apps",)
    )
    with (
        selected(world, sibling=True),
        credential(world, team=team, foreign=case == "foreign", scopes=scopes),
    ):
        if case in ("write", "admin"):
            assert create(world).ok
        else:
            with pytest.raises(PermissionDenied):
                create(world)
    assert Pipeline.objects.count() == int(case in ("write", "admin"))


@pytest.mark.parametrize("case", ("team-session", "org-session", "team-token", "org-token", "read-token"))
def test_http_pipeline_creation_uses_org_and_actual_bearer_boundary(world, case):
    grant(world, "TEAM" if case == "team-session" else "ORG")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    client = Client()
    headers = {
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid),
        "HTTP_X_ASTROLIFT_TEAM": str(world.platform.pk),
    }
    if case.endswith("session"):
        client.force_login(world.user)
        headers["HTTP_X_PLATFORM"] = "WEB"
    else:
        issued = mint_token()
        ApiToken.objects.create(
            user=world.user,
            organization=world.org,
            team=world.medops if case == "team-token" else None,
            name="HTTP pipeline",
            token_hash=issued.token_hash,
            scopes=["read:apps"] if case == "read-token" else ["write:apps"],
        )
        headers["HTTP_AUTHORIZATION"] = f"Bearer {issued.plaintext}"
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        data={
            "query": 'mutation { createPipeline(input: {name: "build", repoUrl: "https://github.com/acme/build"}) { ok errors { code } data { name } } }'
        },
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    payload = response.json()
    allowed = case in ("org-session", "org-token")
    if allowed:
        assert not payload.get("errors"), payload
        assert payload["data"]["createPipeline"]["ok"]
        assert payload["data"]["createPipeline"]["data"]["name"] == "build"
    else:
        assert payload.get("errors") or not payload["data"]["createPipeline"]["ok"], payload
    assert Pipeline.objects.count() == int(allowed)
