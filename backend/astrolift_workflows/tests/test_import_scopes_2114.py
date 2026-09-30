"""Workflow imports cannot borrow selected or bearer scopes to create ORG rows."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken
from astrolift_workflows.schema.import_flow import WorkflowImportMutation
from astrolift_workflows.schema.manifest import WorkflowManifestMutation, WorkflowManifestQuery
from astrolift_workflows.tests.test_manifest_schema import VALID_TOML
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user
from workflows.manifest import create_definition_from_manifest, parse_workflow_manifest
from workflows.models import WorkflowDefinition, WorkflowStage
from workflows.tests.importer_fixtures import flowise_gate

pytestmark = pytest.mark.django_db
CHANGED_SHAPE = VALID_TOML + '\n[[stage]]\nkind = "human_gate"\nrole = "Review"\n'


@pytest.fixture(autouse=True)
def no_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )


@pytest.fixture
def world():
    w = ScopeWorld("2114")
    w.user = make_user("2114")
    w.info = make_info(w.user)
    return w


def grant(w, *permissions, kind="PROJECT", scope_id=None):
    row = {"PROJECT": w.medops_project, "TEAM": w.medops, "APP": w.medops_app, "ORG": w.org}[kind]
    return bind_role(
        w.user, permissions=permissions, kind=kind, scope_id=scope_id or row.pk, slug=f"import-{uuid4().hex}"
    )


def tenant(w):
    return tenant_context(
        TenantContext(
            organization_id=w.org.pk,
            actor_user_id=w.user.pk,
            team_id=w.platform.pk,
            project_id=w.platform_project.pk,
        )
    )


@contextmanager
def token(w, *, owner="own", scope="admin", operator=False):
    if operator:
        w.user.is_superuser = True
        w.user.save()
    organization = w.org
    if owner == "foreign":
        organization = ScopeWorld("2114-foreign-token").org
    team = {"own": w.medops, "sibling": w.platform}.get(owner)
    credential = ApiToken.objects.create(
        user=w.user,
        organization=organization,
        team=team,
        name="import",
        token_hash=uuid4().hex * 2,
        token_last_4="2114",
        scopes=[scope],
    )
    marker = set_current_api_token(credential)
    try:
        yield credential
    finally:
        reset_current_api_token(marker)


def run_import(w, route, *, preview=False, replace=False, toml=VALID_TOML):
    if route == "flow":
        return WorkflowImportMutation().import_workflow_flow(
            w.info, format="flowise", payload=flowise_gate(), preview=preview
        )
    return WorkflowManifestMutation().import_workflow_manifest(
        w.info, toml=toml, preview=preview, replace=replace
    )


def owned(w, *, project=None):
    definition = create_definition_from_manifest(parse_workflow_manifest(VALID_TOML), organization=w.org)
    definition.project = project or w.medops_project
    definition.save()
    return definition


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_pure_manifest_preview_accepts_a_read_grant_anywhere_without_writing(world, kind):
    grant(world, Permission.WORKFLOW_READ, kind=kind)
    with tenant(world):
        result = WorkflowManifestQuery().preview_workflow_manifest(world.info, toml=VALID_TOML)
    assert result.ok
    assert result.definition.slug == "feature-dev"
    assert not WorkflowDefinition.objects.filter(organization=world.org).exists()
    assert not WorkflowStage.objects.filter(definition__organization=world.org).exists()


def test_preview_cannot_borrow_a_foreign_org_grant(world):
    foreign = ScopeWorld("2114-foreign-role")
    grant(world, Permission.WORKFLOW_READ, kind="ORG", scope_id=foreign.org.pk)
    with tenant(world), pytest.raises(PermissionDenied):
        WorkflowManifestQuery().preview_workflow_manifest(world.info, toml=VALID_TOML)


@pytest.mark.parametrize("route", ["flow", "manifest"])
@pytest.mark.parametrize("preview", [True, False])
@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG", "FOREIGN"])
def test_imports_keep_create_permission_at_their_org_destination(world, route, preview, kind):
    if kind == "FOREIGN":
        other = ScopeWorld("2114-foreign-create")
        grant(world, Permission.WORKFLOW_CREATE, kind="ORG", scope_id=other.org.pk)
    else:
        grant(world, Permission.WORKFLOW_CREATE, kind=kind)
    with tenant(world):
        if kind == "ORG":
            result = run_import(world, route, preview=preview)
            assert result.ok
        else:
            with pytest.raises(PermissionDenied):
                run_import(world, route, preview=preview)
    rows = WorkflowDefinition.objects.filter(organization=world.org)
    assert rows.count() == int(kind == "ORG" and not preview)
    if rows.exists():
        assert rows.get().project_id is None
        assert not rows.get().is_enabled
    else:
        assert not WorkflowStage.objects.filter(definition__organization=world.org).exists()


@pytest.mark.parametrize("route", ["flow", "manifest"])
@pytest.mark.parametrize(
    "owner,scope,allowed",
    [
        ("org", "admin", True),
        ("org", "workflow:write", True),
        ("org", "write:apps", True),
        ("org", "read:apps", False),
        ("own", "admin", False),
        ("sibling", "admin", False),
        ("foreign", "admin", False),
    ],
)
@pytest.mark.parametrize("operator", [False, True])
def test_org_import_bearers_keep_org_team_and_verb_ceilings(world, route, owner, scope, allowed, operator):
    grant(world, Permission.WORKFLOW_CREATE, kind="ORG")
    with tenant(world), token(world, owner=owner, scope=scope, operator=operator):
        if allowed:
            assert run_import(world, route).ok
        else:
            with pytest.raises(PermissionDenied):
                run_import(world, route)
    assert WorkflowDefinition.objects.filter(organization=world.org).count() == int(allowed)


@pytest.mark.parametrize("kind", ["PROJECT", "TEAM", "ORG"])
@pytest.mark.parametrize("sibling", [False, True])
def test_compatible_replace_retains_and_authorizes_its_actual_project_owner(world, kind, sibling):
    definition = owned(world, project=world.platform_project if sibling else world.medops_project)
    grant(world, Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE, kind=kind)
    before = definition.name
    with tenant(world):
        if sibling and kind != "ORG":
            with pytest.raises(PermissionDenied):
                run_import(
                    world,
                    "manifest",
                    replace=True,
                    toml=VALID_TOML.replace('name = "Feature Dev"', 'name = "Edited"'),
                )
        else:
            result = run_import(
                world,
                "manifest",
                replace=True,
                toml=VALID_TOML.replace('name = "Feature Dev"', 'name = "Edited"'),
            )
            assert result.ok
            assert result.mode == "updated_in_place"
    definition.refresh_from_db()
    assert definition.project_id == (world.platform_project.pk if sibling else world.medops_project.pk)
    assert definition.name == (before if sibling and kind != "ORG" else "Edited")
    assert WorkflowDefinition.objects.filter(organization=world.org).count() == 1
    assert definition.stages.count() == 1


@pytest.mark.parametrize("permission", [Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE])
def test_replace_requires_both_declared_create_and_existing_update(world, permission):
    definition = owned(world)
    grant(world, permission)
    with tenant(world), pytest.raises(PermissionDenied):
        run_import(world, "manifest", replace=True)
    assert WorkflowDefinition.objects.filter(organization=world.org).count() == 1
    definition.refresh_from_db()
    assert definition.name == "Feature Dev"


@pytest.mark.parametrize("kind,allowed", [("PROJECT", False), ("TEAM", False), ("ORG", True)])
def test_shape_changed_replace_creates_an_org_version_and_requires_org_create(world, kind, allowed):
    definition = owned(world)
    grant(world, Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE, kind=kind)
    with tenant(world):
        if allowed:
            result = run_import(world, "manifest", replace=True, toml=CHANGED_SHAPE)
            assert result.mode == "versioned"
            created = WorkflowDefinition.objects.get(organization=world.org, slug=result.created_slug)
            assert created.project_id is None
        else:
            with pytest.raises(PermissionDenied):
                run_import(world, "manifest", replace=True, toml=CHANGED_SHAPE)
    assert WorkflowDefinition.objects.filter(organization=world.org).count() == (2 if allowed else 1)
    assert definition.stages.count() == 1


@pytest.mark.parametrize(
    "owner,scope,allowed",
    [
        ("own", "admin", True),
        ("own", "workflow:write", True),
        ("own", "write:apps", True),
        ("own", "read:apps", False),
        ("sibling", "admin", False),
        ("foreign", "admin", False),
        ("org", "admin", True),
    ],
)
@pytest.mark.parametrize("operator", [False, True])
def test_project_replace_bearers_cannot_cross_their_owner_ceiling(world, owner, scope, allowed, operator):
    definition = owned(world)
    grant(world, Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE, kind="ORG")
    with tenant(world), token(world, owner=owner, scope=scope, operator=operator):
        if allowed:
            assert run_import(world, "manifest", replace=True).ok
        else:
            with pytest.raises(PermissionDenied):
                run_import(world, "manifest", replace=True)
    assert WorkflowDefinition.objects.filter(organization=world.org).count() == 1
    assert definition.stages.count() == 1


@pytest.mark.parametrize("stale", ["project", "team", "foreign_team", "foreign_project"])
@pytest.mark.parametrize("kind", ["PROJECT", "ORG"])
def test_stale_or_incoherent_replace_owner_falls_back_to_explicit_org(world, stale, kind):
    definition = owned(world)
    grant(world, Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE, kind=kind)
    if stale == "project":
        world.medops_project.deleted_at = timezone.now()
        world.medops_project.save()
    elif stale == "team":
        world.medops.deleted_at = timezone.now()
        world.medops.save()
    else:
        other = ScopeWorld("2114-stale")
        if stale == "foreign_project":
            definition.project = other.medops_project
            definition.save()
        else:
            world.medops_project.team = other.medops
            world.medops_project.save()
    with tenant(world):
        if kind == "ORG":
            assert run_import(world, "manifest", replace=True).ok
        else:
            with pytest.raises(PermissionDenied):
                run_import(world, "manifest", replace=True)
    assert WorkflowDefinition.objects.filter(organization=world.org).count() == 1


def test_replacement_rechecks_destination_after_admission_before_any_write(world, monkeypatch):
    import workflows.manifest as manifest

    definition = owned(world)
    grant(world, Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE)
    original = manifest.shape_compatible
    calls = []

    def changed_after_admission(existing, parsed):
        calls.append(True)
        if len(calls) == 2:
            existing.stages.filter(order=0).update(kind=WorkflowStage.StageKind.HUMAN_GATE)
        return original(existing, parsed)

    monkeypatch.setattr(manifest, "shape_compatible", changed_after_admission)
    with tenant(world), pytest.raises(PermissionDenied):
        run_import(world, "manifest", replace=True)
    assert len(calls) == 2
    assert WorkflowDefinition.objects.filter(organization=world.org).count() == 1
    # The simulated concurrent shape change took place in the handler's
    # transaction and was rolled back along with the refused replacement.
    assert definition.stages.get().kind == WorkflowStage.StageKind.AGENT_DISPATCH


@pytest.mark.parametrize("route", ["flow", "manifest"])
@pytest.mark.parametrize("case", ["project-session", "org-session", "team-token", "org-token", "read-token"])
def test_http_import_uses_actual_org_destination_and_bearer_ceiling(world, route, case):
    from django.conf import settings
    from django.test import Client

    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import Member

    grant(world, Permission.WORKFLOW_CREATE, kind="PROJECT" if case == "project-session" else "ORG")
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
            name="HTTP import",
            token_hash=issued.token_hash,
            scopes=["read:apps"] if case == "read-token" else ["workflow:write"],
        )
        headers["HTTP_AUTHORIZATION"] = f"Bearer {issued.plaintext}"
    if route == "flow":
        field = "importWorkflowFlow"
        query = 'mutation Import($payload: JSON!) { importWorkflowFlow(format: "flowise", payload: $payload, preview: false) { ok createdSlug } }'
        variables = {"payload": flowise_gate()}
    else:
        field = "importWorkflowManifest"
        query = "mutation Import($toml: String!) { importWorkflowManifest(toml: $toml, preview: false) { ok createdSlug } }"
        variables = {"toml": VALID_TOML}
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        data={"query": query, "variables": variables},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    payload = response.json()
    allowed = case in {"org-session", "org-token"}
    if allowed:
        assert not payload.get("errors"), payload
        assert payload["data"][field]["ok"]
    else:
        assert payload.get("errors") or not payload["data"][field]["ok"], payload
    assert WorkflowDefinition.objects.filter(organization=world.org).count() == int(allowed)


def test_http_pure_preview_accepts_local_read_and_never_writes(world):
    from django.conf import settings
    from django.test import Client

    from astrolift_identity.models import Member

    grant(world, Permission.WORKFLOW_READ)
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    client = Client(
        HTTP_X_PLATFORM="WEB",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_ASTROLIFT_TEAM=str(world.platform.pk),
    )
    client.force_login(world.user)
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        data={
            "query": "query Preview($toml: String!) { previewWorkflowManifest(toml: $toml) { ok definition { slug } } }",
            "variables": {"toml": VALID_TOML},
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    payload = response.json()
    assert not payload.get("errors"), payload
    assert payload["data"]["previewWorkflowManifest"]["ok"]
    assert not WorkflowDefinition.objects.filter(organization=world.org).exists()
