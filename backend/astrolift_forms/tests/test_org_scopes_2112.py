"""Real grants and bearer ceilings for organization-owned forms."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.conf import settings
from django.test import Client

from astrolift_forms.models import FormDefinition, FormSubmission
from astrolift_forms.schema.mutations import (
    DeleteFormDefinitionInput,
    FormDefinitionInput,
    FormDefinitionUpdateInput,
    FormsMutation,
)
from astrolift_forms.schema.queries import FormsQuery
from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db

READ_ROUTES = ("form_definitions", "form_definition", "form_submissions")
WRITE_ROUTES = (
    "create_form_definition",
    "update_form_definition",
    "publish_form",
    "archive_form",
    "delete_form_definition",
    "update_submission_status",
    "submit_form",
)
ROUTES = (*READ_ROUTES, *WRITE_ROUTES)
PERMISSIONS = (
    Permission.FORM_READ,
    Permission.FORM_CREATE,
    Permission.FORM_UPDATE,
    Permission.FORM_DELETE,
    Permission.FORM_MODERATE,
    Permission.FORM_SUBMIT,
)


@pytest.fixture(name="world")
def _form_world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    world = ScopeWorld("forms-2112")
    world.user = make_user("forms-2112")
    world.other_org = Organization.objects.create(name="Other", slug="other-forms-2112")
    world.form = FormDefinition.objects.create(
        organization=world.org, name="Intake", slug="intake-2112", status="published", schema={}
    )
    world.foreign_form = FormDefinition.objects.create(
        organization=world.other_org, name="Foreign", slug="foreign-2112", status="published", schema={}
    )
    world.submission = FormSubmission.objects.create(
        organization=world.org, form=world.form, form_version=1, payload={"private": "own"}
    )
    world.foreign_submission = FormSubmission.objects.create(
        organization=world.other_org, form=world.foreign_form, form_version=1, payload={"private": "foreign"}
    )
    return world


def _tenant(world, selected="own", *, actor=True, org=None):
    return tenant_context(_tenant_value(world, selected, actor=actor, org=org))


def _tenant_value(world, selected="own", *, actor=True, org=None):
    team = world.medops if selected == "own" else world.platform
    project = world.medops_project if selected == "own" else world.platform_project
    return TenantContext(
        organization_id=(org or world.org).pk,
        actor_user_id=world.user.pk if actor else None,
        team_id=team.pk,
        project_id=project.pk,
    )


def _grant(world, kind="ORG", *, foreign=False):
    return bind_role(
        world.user,
        permissions=PERMISSIONS,
        kind=kind,
        scope_id=world.other_org.pk
        if foreign
        else {"ORG": world.org.pk, "TEAM": world.medops.pk, "PROJECT": world.medops_project.pk}[kind],
        slug=f"forms-{uuid4().hex}",
    )


def _make_token(world, *, ceiling="org"):
    return ApiToken.objects.create(
        user=world.user,
        organization=world.other_org if ceiling == "foreign-org" else world.org,
        team=world.medops if ceiling in {"team", "operator-team"} else None,
        name="forms 2112",
        token_hash=uuid4().hex,
        scopes=["read:apps"] if ceiling == "read-apps" else ["admin"],
    )


@contextmanager
def _token(world, *, ceiling="org"):
    handle = set_current_api_token(_make_token(world, ceiling=ceiling))
    try:
        yield
    finally:
        reset_current_api_token(handle)


def _invoke(world, name, *, form=None, submission=None):
    form = form or world.form
    submission = submission or world.submission
    kwargs = {
        "form_definitions": {},
        "form_definition": {"slug": form.slug},
        "form_submissions": {"slug": form.slug},
        "create_form_definition": {"input": FormDefinitionInput(name="New", slug="new-form-2112", schema={})},
        "update_form_definition": {"input": FormDefinitionUpdateInput(slug=form.slug, name="Edited")},
        "publish_form": {"slug": form.slug},
        "archive_form": {"slug": form.slug},
        "delete_form_definition": {"input": DeleteFormDefinitionInput(slug=form.slug)},
        "update_submission_status": {"submission_id": str(submission.guid), "status": "reviewed"},
        "submit_form": {"slug": form.slug, "payload": {"new": "response"}},
    }[name]
    cls = FormsQuery if name in READ_ROUTES else FormsMutation
    return getattr(cls(), name)(make_info(world.user), **kwargs)


def _rows():
    return [list(model.all_objects.order_by("pk").values()) for model in (FormDefinition, FormSubmission)]


def _denied(world, name, **kwargs):
    before = _rows()
    if name in READ_ROUTES:
        with pytest.raises(PermissionDenied):
            _invoke(world, name, **kwargs)
    else:
        result = _invoke(world, name, **kwargs)
        assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result
    assert _rows() == before


@pytest.mark.parametrize("name", ROUTES)
@pytest.mark.parametrize(
    "kind,selected,foreign",
    [
        ("TEAM", "own", False),
        ("TEAM", "sibling", False),
        ("PROJECT", "own", False),
        ("ORG", "own", True),
    ],
)
def test_suborg_and_foreign_org_grants_cannot_reach_forms(world, name, kind, selected, foreign):
    _grant(world, kind, foreign=foreign)
    with _tenant(world, selected):
        _denied(world, name)


@pytest.mark.parametrize("name", ROUTES)
@pytest.mark.parametrize("selected", ["own", "sibling"])
def test_org_grant_allows_forms_independent_of_selected_team(world, name, selected):
    _grant(world)
    with _tenant(world, selected):
        result = _invoke(world, name)
    if name == "form_definitions":
        assert [str(row.id) for row in result] == [str(world.form.guid)]
    elif name == "form_definition":
        assert str(result.id) == str(world.form.guid)
    elif name == "form_submissions":
        assert [str(row.id) for row in result] == [str(world.submission.guid)]
    else:
        assert result.ok, result.errors
        if name == "delete_form_definition":
            assert not FormDefinition.objects.filter(pk=world.form.pk).exists()
            assert not FormSubmission.objects.filter(pk=world.submission.pk).exists()
        elif name == "update_submission_status":
            world.submission.refresh_from_db()
            assert world.submission.status == "reviewed"
        elif name == "submit_form":
            assert FormSubmission.objects.filter(form=world.form).count() == 2


@pytest.mark.parametrize("name", ROUTES)
@pytest.mark.parametrize("ceiling", ["team", "operator-team", "foreign-org", "read-apps"])
def test_bearer_ceiling_cannot_borrow_owner_organization_authority(world, name, ceiling):
    _grant(world)
    if ceiling == "operator-team":
        world.user.is_superuser = True
        world.user.save(update_fields=["is_superuser"])
    with _tenant(world, "sibling"), _token(world, ceiling=ceiling):
        _denied(world, name)


@pytest.mark.parametrize("name", ROUTES)
def test_org_admin_bearer_preserves_form_behavior(world, name):
    _grant(world)
    with _tenant(world), _token(world):
        result = _invoke(world, name)
    assert result if name in READ_ROUTES else result.ok


@pytest.mark.parametrize(
    "name", ["form_definition", "form_submissions", "update_form_definition", "update_submission_status"]
)
@pytest.mark.parametrize("target", ["foreign", "deleted", "missing"])
def test_target_misses_never_fall_back_to_selected_team(world, name, target):
    _grant(world, "TEAM")
    form, submission = (
        (world.foreign_form, world.foreign_submission)
        if target == "foreign"
        else (world.form, world.submission)
    )
    if target == "deleted":
        form.soft_delete()
        submission.soft_delete()
    elif target == "missing":
        form.slug = "missing-2112"
        submission.guid = uuid4()
    with _tenant(world):
        _denied(world, name, form=form, submission=submission)


def test_moderation_refuses_inconsistent_or_deleted_form_ownership(world):
    _grant(world)
    corrupt = FormSubmission.objects.create(
        organization=world.org, form=world.foreign_form, form_version=1, payload={"foreign": "private"}
    )
    deleted = FormDefinition.objects.create(organization=world.org, name="Deleted", slug="deleted-2112")
    orphan = FormSubmission.objects.create(organization=world.org, form=deleted, form_version=1, payload={})
    deleted.soft_delete()
    with _tenant(world):
        for submission in (world.foreign_submission, corrupt, orphan):
            result = _invoke(world, "update_submission_status", submission=submission)
            assert not result.ok and result.errors[0].code == "NOT_FOUND"
            submission.refresh_from_db()
            assert submission.status == "new"


def test_form_delete_does_not_change_a_submission_owned_by_another_organization(world):
    _grant(world)
    corrupt = FormSubmission.objects.create(
        organization=world.other_org, form=world.form, form_version=1, payload={"foreign": "private"}
    )
    with _tenant(world):
        result = _invoke(world, "delete_form_definition")
    assert result.ok, result.errors
    assert not FormSubmission.objects.filter(pk=world.submission.pk).exists()
    corrupt.refresh_from_db()
    assert corrupt.deleted_at is None


def test_submission_collection_excludes_foreign_owner_and_deleted_rows(world):
    _grant(world)
    FormSubmission.objects.create(
        organization=world.other_org,
        form=world.form,
        form_version=1,
        payload={"foreign": "secret"},
    )
    deleted = FormSubmission.objects.create(
        organization=world.org, form=world.form, form_version=1, payload={"deleted": "secret"}
    )
    deleted.soft_delete()
    with _tenant(world):
        rows = _invoke(world, "form_submissions")
    assert [(str(row.id), row.payload) for row in rows] == [(str(world.submission.guid), {"private": "own"})]


def test_published_public_submission_retains_anonymous_admission_without_read_access(world):
    world.form.is_public = True
    world.form.save(update_fields=["is_public"])
    with _tenant(world, actor=False):
        result = _invoke(world, "submit_form")
        assert result.ok, result.errors
        with pytest.raises(PermissionDenied):
            _invoke(world, "form_submissions")
    submitted = FormSubmission.objects.get(guid=str(result.data.id))
    assert submitted.submitter is None


@pytest.mark.parametrize(
    "credential", ["team-session", "org-session", "team-token", "org-token", "read-token"]
)
@pytest.mark.parametrize("mutation", [False, True])
def test_http_form_access_keeps_session_and_bearer_scope_ceilings(world, credential, mutation):
    _grant(world, "TEAM" if credential == "team-session" else "ORG")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    client = Client()
    headers = {
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid),
        "HTTP_X_ASTROLIFT_TEAM": str(world.platform.pk),
    }
    if credential.endswith("session"):
        client.force_login(world.user)
        headers["HTTP_X_PLATFORM"] = "WEB"
    else:
        issued = mint_token()
        token = _make_token(
            world,
            ceiling="team"
            if credential == "team-token"
            else "read-apps"
            if credential == "read-token"
            else "org",
        )
        token.token_hash = issued.token_hash
        token.save(update_fields=["token_hash"])
        headers["HTTP_AUTHORIZATION"] = f"Bearer {issued.plaintext}"
    data = (
        {"query": 'mutation { archiveForm(slug: "intake-2112") { ok errors { code } } }'}
        if mutation
        else {"query": '{ formDefinitions { id } formSubmissions(slug: "intake-2112") { id payload } }'}
    )
    response = client.post(
        f"/{settings.BASE_URL}gql/config/", data=data, content_type="application/json", **headers
    )
    assert response.status_code == 200, response.content
    payload = response.json()
    allowed = credential in {"org-session", "org-token"}
    if mutation:
        assert not payload.get("errors"), payload
        result = payload["data"]["archiveForm"]
        assert result["ok"] is allowed
        world.form.refresh_from_db()
        assert world.form.status == ("archived" if allowed else "published")
        if not allowed:
            assert result["errors"] == [{"code": "PERMISSION_DENIED"}]
    elif allowed:
        assert not payload.get("errors"), payload
        assert payload["data"]["formDefinitions"] == [{"id": str(world.form.guid)}]
        assert payload["data"]["formSubmissions"] == [
            {"id": str(world.submission.guid), "payload": {"private": "own"}}
        ]
    else:
        assert payload.get("errors") and payload.get("data") is None, payload
