"""Real PostgreSQL owner, bearer, HTTP and dispatch boundaries for pipeline secrets."""

from contextlib import contextmanager

import pytest
from django.conf import settings
from django.test import Client

from astrolift_graphql import GUID
from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member
from astrolift_lifecycle.models import OrgSecret
from astrolift_lifecycle.services.secrets import write_org_secret
from astrolift_pipelines.models import Pipeline, PipelineRun
from astrolift_pipelines.pipeline_secrets import _secret_key, set_pipeline_secret
from astrolift_pipelines.schema.mutations import (
    DeletePipelineSecretInput,
    PipelinesMutation,
    SetPipelineSecretInput,
)
from astrolift_pipelines.schema.queries import PipelinesQuery
from astrolift_pipelines.secret_plumbing import SecretResolutionError, resolve_pipeline_secrets
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db
PERMISSIONS = [Permission.SECRET_LIST, Permission.SECRET_WRITE, Permission.PIPELINE_SECRET_MANAGE]
SYNTHETIC = "synthetic-fixture-secret-2171"


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    world = ScopeWorld("secret2171")
    world.foreign = ScopeWorld("secret2171-foreign")
    world.user = make_user("secret2171")
    world.pipeline = Pipeline.objects.create(
        organization=world.org,
        registered_app=world.medops_app,
        name="build",
        repo_url="https://example.test/repo",
    )
    world.other = Pipeline.objects.create(
        organization=world.org,
        registered_app=world.platform_app,
        name="other",
        repo_url="https://example.test/other",
    )
    world.orphan = Pipeline.objects.create(
        organization=world.org, name="orphan", repo_url="https://example.test/orphan"
    )
    return world


def selected(world):
    return tenant_context(
        TenantContext(
            organization_id=world.org.pk,
            actor_user_id=world.user.pk,
            team_id=world.platform.pk,
            project_id=world.platform_project.pk,
        )
    )


def grant(world, kind="APP", *, sibling=False, foreign=False, permissions=PERMISSIONS):
    owner = world.foreign if foreign else world
    row = {
        "ORG": owner.org,
        "TEAM": owner.platform if sibling else owner.medops,
        "PROJECT": owner.platform_project if sibling else owner.medops_project,
        "APP": owner.platform_app if sibling else owner.medops_app,
    }[kind]
    bind_role(world.user, permissions=permissions, kind=kind, scope_id=row.pk, slug="secret2171-role")


def write(world, pipeline=None, *, name="TOKEN"):
    return PipelinesMutation().set_pipeline_secret(
        make_info(world.user),
        input=SetPipelineSecretInput(
            pipeline_id=GUID(str((pipeline or world.pipeline).guid)), name=name, value=SYNTHETIC
        ),
    )


def remove(world, pipeline=None, *, name="TOKEN"):
    return PipelinesMutation().delete_pipeline_secret(
        make_info(world.user),
        input=DeletePipelineSecretInput(pipeline_id=GUID(str((pipeline or world.pipeline).guid)), name=name),
    )


def metadata(world, pipeline=None):
    return PipelinesQuery().astrolift_pipeline_secrets(
        make_info(world.user), pipeline_id=GUID(str((pipeline or world.pipeline).guid))
    )


@contextmanager
def bearer(world, *, team=None, foreign=False, scopes=("secret:write", "read:apps")):
    issued = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.foreign.org if foreign else world.org,
        team=team,
        name="secret fixture",
        token_hash=issued.token_hash,
        scopes=list(scopes),
    )
    handle = set_current_api_token(token)
    try:
        yield issued
    finally:
        reset_current_api_token(handle)


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_actual_owner_allows_crud_with_sibling_selected_and_isolates_namespace(world, kind, monkeypatch):
    grant(world, kind)
    set_pipeline_secret(world.other, "TOKEN", "other-pipeline-fixture")
    with selected(world):
        assert write(world, name=" TOKEN ").ok
        row = OrgSecret.objects.get(key=_secret_key(world.pipeline, "TOKEN"))
        assert SYNTHETIC.encode() not in bytes(row.ciphertext)
        monkeypatch.setattr(
            "astrolift_lifecycle.services.secrets.decrypt",
            lambda *a: pytest.fail("metadata must not decrypt"),
        )
        rows = metadata(world)
        assert [(r.name, str(r.id)) for r in rows] == [("TOKEN", str(row.guid))]
        assert remove(world).ok
        assert metadata(world) == []
    assert OrgSecret.all_objects.get(pk=row.pk).deleted_at is not None
    assert OrgSecret.objects.filter(key=_secret_key(world.other, "TOKEN")).exists()


@pytest.mark.parametrize("kind,foreign", [("APP", False), ("PROJECT", False), ("TEAM", False), ("ORG", True)])
def test_sibling_or_foreign_role_cannot_read_write_delete(world, kind, foreign):
    grant(world, kind, sibling=True, foreign=foreign)
    set_pipeline_secret(world.pipeline, "TOKEN", SYNTHETIC)
    before = bytes(OrgSecret.objects.get().ciphertext)
    with selected(world):
        with pytest.raises(PermissionDenied):
            metadata(world)
        assert not write(world).ok
        assert not remove(world).ok
    assert bytes(OrgSecret.objects.get().ciphertext) == before


@pytest.mark.parametrize(
    "permissions", [[Permission.SECRET_WRITE], [Permission.PIPELINE_SECRET_MANAGE], [Permission.SECRET_LIST]]
)
def test_each_write_permission_is_required(world, permissions):
    grant(world, permissions=permissions)
    with selected(world):
        assert not write(world).ok
    assert not OrgSecret.objects.exists()


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_unassociated_pipeline_requires_explicit_org(world, kind):
    grant(world, kind)
    with selected(world):
        result = write(world, world.orphan)
        assert result.ok == (kind == "ORG")
        if kind == "ORG":
            assert [r.name for r in metadata(world, world.orphan)] == ["TOKEN"]
        else:
            with pytest.raises(PermissionDenied):
                metadata(world, world.orphan)
    assert OrgSecret.objects.count() == int(kind == "ORG")


@pytest.mark.parametrize(
    "case",
    [
        "own-team",
        "sibling-team",
        "org",
        "admin-own-team",
        "admin-sibling-team",
        "operator-sibling-team",
        "foreign",
        "write-apps",
        "read-only",
    ],
)
def test_bearer_owner_and_scope_ceiling_even_for_admin(world, case):
    grant(world, "ORG")
    if case == "operator-sibling-team":
        world.user.is_superuser = True
        world.user.save(update_fields=["is_superuser"])
    team = (
        world.medops
        if case in ["own-team", "admin-own-team"]
        else world.platform
        if "sibling-team" in case
        else None
    )
    scopes = (
        ("admin",)
        if case.startswith(("admin", "operator"))
        else ("write:apps",)
        if case == "write-apps"
        else ("read:apps",)
        if case == "read-only"
        else ("secret:write", "read:apps")
    )
    allowed = case in ["own-team", "org", "admin-own-team"]
    with selected(world), bearer(world, team=team, foreign=case == "foreign", scopes=scopes):
        assert write(world).ok == allowed
        if team is not None:
            assert not write(world, world.orphan).ok
    assert OrgSecret.objects.count() == int(allowed)


@pytest.mark.parametrize(
    "damage",
    [
        "pipeline",
        "org",
        "app",
        "team",
        "project",
        "foreign-app",
        "foreign-team",
        "foreign-project",
        "incoherent-team",
    ],
)
def test_stale_or_foreign_owner_never_touches_storage_or_dispatch(world, damage, monkeypatch):
    grant(world, "ORG")
    set_pipeline_secret(world.pipeline, "TOKEN", SYNTHETIC)
    run = PipelineRun.objects.create(pipeline=world.pipeline, run_number=1, trigger_kind="manual")
    # Cache the old owner before changing its live ancestry.
    _ = run.pipeline.organization
    if damage in ["pipeline", "org", "app", "team", "project"]:
        {
            "pipeline": world.pipeline,
            "org": world.org,
            "app": world.medops_app,
            "team": world.medops,
            "project": world.medops_project,
        }[damage].soft_delete()
    elif damage == "foreign-app":
        Pipeline.objects.filter(pk=world.pipeline.pk).update(registered_app=world.foreign.medops_app)
    else:
        update = {
            "foreign-team": {"team": world.foreign.medops},
            "foreign-project": {"project": world.foreign.medops_project},
            "incoherent-team": {"team": world.platform},
        }[damage]
        type(world.medops_app).objects.filter(pk=world.medops_app.pk).update(**update)
    monkeypatch.setattr(
        "astrolift_lifecycle.services.secrets.write_org_secret",
        lambda *a: pytest.fail("invalid owner must not write"),
    )
    monkeypatch.setattr(
        "astrolift_pipelines.secret_plumbing._read_org_secret",
        lambda *a: pytest.fail("invalid owner must not reveal"),
    )
    with selected(world):
        result = write(world)
        assert not result.ok
        try:
            assert metadata(world) == []
        except PermissionDenied:
            pass
    with pytest.raises(SecretResolutionError):
        resolve_pipeline_secrets(run, ["TOKEN"])
    assert OrgSecret.objects.count() == 1


@pytest.mark.parametrize("name", ["", " ", "../TOKEN", "path/TOKEN", "nul\x00name", "x" * 513])
def test_invalid_names_cannot_write_or_delete(world, name):
    grant(world)
    with selected(world):
        for result in [write(world, name=name), remove(world, name=name)]:
            assert not result.ok
            assert result.errors[0].code == "VALIDATION"
    assert not OrgSecret.objects.exists()


def test_dispatch_reads_the_saved_pipeline_namespace_without_bare_or_sibling_fallback(world):
    grant(world)
    run = PipelineRun.objects.create(pipeline=world.pipeline, run_number=1, trigger_kind="manual")
    with selected(world):
        assert write(world).ok
    write_org_secret(world.org, "TOKEN", "bare-fixture")
    set_pipeline_secret(world.other, "TOKEN", "sibling-fixture")
    assert resolve_pipeline_secrets(run, ["TOKEN"]) == {"TOKEN": SYNTHETIC}
    with selected(world):
        assert remove(world).ok
    with pytest.raises(SecretResolutionError):
        resolve_pipeline_secrets(run, ["TOKEN"])
    run.soft_delete()
    with pytest.raises(SecretResolutionError):
        resolve_pipeline_secrets(run, [])


def test_owner_is_rechecked_after_entry_gate(world, monkeypatch):
    grant(world)
    from astrolift_pipelines.schema import mutations

    original = mutations._change_secret

    def changed(info, input, **kwargs):
        Pipeline.objects.filter(pk=world.pipeline.pk).update(registered_app=world.platform_app)
        return original(info, input, **kwargs)

    monkeypatch.setattr(mutations, "_change_secret", changed)
    with selected(world):
        assert not write(world).ok
    assert not OrgSecret.objects.exists()


def test_backend_failure_is_typed_sanitized_and_does_not_commit(world, monkeypatch):
    grant(world)

    def failed(org, key, value):
        write_org_secret(org, key, value)
        raise RuntimeError(SYNTHETIC)

    monkeypatch.setattr("astrolift_lifecycle.services.secrets.write_org_secret", failed)
    with selected(world):
        result = write(world)
    assert not result.ok and result.errors[0].code == "INTERNAL"
    assert SYNTHETIC not in result.errors[0].message
    assert not OrgSecret.objects.exists()


@pytest.mark.parametrize(
    "case",
    ["session-owner", "session-sibling", "token-owner", "token-sibling", "token-foreign", "token-read"],
)
def test_http_typed_envelopes_metadata_only_and_no_write_on_denial(world, case):
    grant(world, "APP" if case == "session-sibling" else "ORG", sibling=case == "session-sibling")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    client = Client()
    headers = {
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid),
        "HTTP_X_ASTROLIFT_TEAM": str(world.platform.pk),
    }
    if case.startswith("session"):
        client.force_login(world.user)
        headers["HTTP_X_PLATFORM"] = "WEB"
    else:
        issued = mint_token()
        ApiToken.objects.create(
            user=world.user,
            organization=world.foreign.org if case == "token-foreign" else world.org,
            team=world.platform if case == "token-sibling" else world.medops,
            name="HTTP secret fixture",
            token_hash=issued.token_hash,
            scopes=["read:apps"] if case == "token-read" else ["secret:write", "read:apps"],
        )
        headers["HTTP_AUTHORIZATION"] = f"Bearer {issued.plaintext}"
    query = "mutation Save($input: SetPipelineSecretInput!) { setPipelineSecret(input: $input) { ok errors { code message } data { pipelineId name } } }"
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        data={
            "query": query,
            "variables": {
                "input": {"pipelineId": str(world.pipeline.guid), "name": "TOKEN", "value": SYNTHETIC}
            },
        },
        content_type="application/json",
        **headers,
    )
    if case == "token-foreign":
        assert response.status_code == 401
        assert not OrgSecret.objects.exists()
        return
    assert response.status_code == 200, response.content
    payload = response.json()
    allowed = case in ["session-owner", "token-owner"]
    assert SYNTHETIC not in response.content.decode()
    if case == "token-foreign":
        assert payload.get("errors") or not payload["data"]["setPipelineSecret"]["ok"], payload
    else:
        assert not payload.get("errors"), payload
        assert payload["data"]["setPipelineSecret"]["ok"] == allowed
        if not allowed:
            assert payload["data"]["setPipelineSecret"]["errors"][0]["code"] == "PERMISSION_DENIED"
    assert OrgSecret.objects.count() == int(allowed)
    if allowed:
        response = client.post(
            f"/{settings.BASE_URL}gql/config/",
            data={
                "query": "query Names($id: GUID!) { astroliftPipelineSecrets(pipelineId: $id) { id name createdAt updatedAt } }",
                "variables": {"id": str(world.pipeline.guid)},
            },
            content_type="application/json",
            **headers,
        )
        assert not response.json().get("errors"), response.content
        assert [r["name"] for r in response.json()["data"]["astroliftPipelineSecrets"]] == ["TOKEN"]
        assert SYNTHETIC not in response.content.decode()


def test_dispatch_refuses_cached_owner_after_pipeline_moves_to_foreign_org(world):
    set_pipeline_secret(world.pipeline, "TOKEN", SYNTHETIC)
    run = PipelineRun.objects.create(pipeline=world.pipeline, run_number=1, trigger_kind="manual")
    _ = run.pipeline.organization
    Pipeline.objects.filter(pk=world.pipeline.pk).update(organization=world.foreign.org)
    with pytest.raises(SecretResolutionError):
        resolve_pipeline_secrets(run, ["TOKEN"])


def test_dispatch_refuses_cached_run_after_database_soft_delete(world):
    run = PipelineRun.objects.create(pipeline=world.pipeline, run_number=1, trigger_kind="manual")
    PipelineRun.objects.get(pk=run.pk).soft_delete()
    with pytest.raises(SecretResolutionError):
        resolve_pipeline_secrets(run, [])
