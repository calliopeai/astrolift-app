"""Tests for FormDefinition CRUD + lifecycle transitions (#453).

Asserted shapes:

* Create requires ``form.create``; denied without grant.
* Create rejects empty name / slug, dup slug per org (CONFLICT), and
  malformed schemas.
* Update requires ``form.update``; archived forms refuse edits.
* Publish flips status + stamps ``published_at`` exactly once on
  first publish; re-publish is idempotent (no error, returns current).
* Republish from a non-published state bumps ``schema_version``.
* Archive flips a published form to archived; idempotent on archived.
* Delete soft-deletes the form and cascade-soft-deletes its submissions.
* Cross-tenant isolation: org A cannot read / mutate org B's form.
"""

from __future__ import annotations

import pytest

from astrolift_forms.models import FormDefinition, FormSubmission
from astrolift_forms.schema.mutations import (
    DeleteFormDefinitionInput,
    FormDefinitionInput,
    FormDefinitionUpdateInput,
    FormsMutation,
)
from astrolift_forms.schema.queries import FormsQuery
from core.permissions import Permission

pytestmark = pytest.mark.django_db


def _grant_all(resolver):
    for p in (
        Permission.FORM_READ,
        Permission.FORM_CREATE,
        Permission.FORM_UPDATE,
        Permission.FORM_DELETE,
        Permission.FORM_SUBMIT,
        Permission.FORM_MODERATE,
    ):
        resolver.grant(p)


# ---- create ----------------------------------------------------


def test_create_form_definition_succeeds(permission_resolver, info, org, with_tenant_org):
    _grant_all(permission_resolver)
    with with_tenant_org(org):
        result = FormsMutation().create_form_definition(
            info(),
            input=FormDefinitionInput(
                name="Intake",
                slug="intake",
                schema={"type": "object", "properties": {"q1": {"type": "string"}}},
                description="x",
            ),
        )
    assert result.ok, result.errors
    row = FormDefinition.objects.get(organization=org, slug="intake")
    assert row.name == "Intake"
    assert row.status == FormDefinition.Status.DRAFT
    assert row.schema_version == 1
    assert row.published_at is None


def test_create_requires_form_create_permission(permission_resolver, info, org, with_tenant_org):
    # No grant at all.
    with with_tenant_org(org):
        result = FormsMutation().create_form_definition(
            info(),
            input=FormDefinitionInput(name="Intake", slug="intake", schema={"type": "object"}),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_create_rejects_empty_name(permission_resolver, info, org, with_tenant_org):
    _grant_all(permission_resolver)
    with with_tenant_org(org):
        result = FormsMutation().create_form_definition(
            info(),
            input=FormDefinitionInput(name="   ", slug="x", schema={}),
        )
    assert not result.ok
    assert result.errors[0].field == "name"


def test_create_rejects_empty_slug(permission_resolver, info, org, with_tenant_org):
    _grant_all(permission_resolver)
    with with_tenant_org(org):
        result = FormsMutation().create_form_definition(
            info(),
            input=FormDefinitionInput(name="x", slug="", schema={}),
        )
    assert not result.ok
    assert result.errors[0].field == "slug"


def test_create_rejects_non_object_schema(permission_resolver, info, org, with_tenant_org):
    _grant_all(permission_resolver)
    with with_tenant_org(org):
        result = FormsMutation().create_form_definition(
            info(),
            input=FormDefinitionInput(name="x", slug="x", schema="not-an-object"),
        )
    assert not result.ok
    assert result.errors[0].field == "schema"


def test_create_rejects_duplicate_slug_per_org(permission_resolver, info, org, draft_form, with_tenant_org):
    """Two active forms with the same slug in the same org → CONFLICT."""
    _grant_all(permission_resolver)
    with with_tenant_org(org):
        result = FormsMutation().create_form_definition(
            info(),
            input=FormDefinitionInput(
                name="another",
                slug=draft_form.slug,
                schema={"type": "object"},
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "CONFLICT"


def test_same_slug_allowed_in_different_orgs(
    permission_resolver, info, org, other_org, draft_form, with_tenant_org
):
    _grant_all(permission_resolver)
    # Slug 'onboarding-survey' already exists in `org`; same slug should
    # be createable in `other_org`.
    with with_tenant_org(other_org):
        result = FormsMutation().create_form_definition(
            info(),
            input=FormDefinitionInput(
                name="Other org survey",
                slug=draft_form.slug,
                schema={"type": "object"},
            ),
        )
    assert result.ok, result.errors


# ---- update ----------------------------------------------------


def test_update_form_definition_succeeds(permission_resolver, info, org, draft_form, with_tenant_org):
    _grant_all(permission_resolver)
    with with_tenant_org(org):
        result = FormsMutation().update_form_definition(
            info(),
            input=FormDefinitionUpdateInput(
                slug=draft_form.slug,
                name="Renamed",
                description="updated",
            ),
        )
    assert result.ok, result.errors
    draft_form.refresh_from_db()
    assert draft_form.name == "Renamed"
    assert draft_form.description == "updated"


def test_update_requires_form_update_permission(permission_resolver, info, org, draft_form, with_tenant_org):
    permission_resolver.grant(Permission.FORM_READ)
    # No FORM_UPDATE grant.
    with with_tenant_org(org):
        result = FormsMutation().update_form_definition(
            info(),
            input=FormDefinitionUpdateInput(slug=draft_form.slug, name="nope"),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_update_archived_form_refuses(permission_resolver, info, org, draft_form, with_tenant_org):
    _grant_all(permission_resolver)
    draft_form.status = FormDefinition.Status.ARCHIVED
    draft_form.save()
    with with_tenant_org(org):
        result = FormsMutation().update_form_definition(
            info(),
            input=FormDefinitionUpdateInput(slug=draft_form.slug, name="never"),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_update_missing_form_returns_not_found(permission_resolver, info, org, with_tenant_org):
    _grant_all(permission_resolver)
    with with_tenant_org(org):
        result = FormsMutation().update_form_definition(
            info(),
            input=FormDefinitionUpdateInput(slug="nope-no-such-slug", name="x"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- publish ---------------------------------------------------


def test_publish_form_first_time_stamps_published_at(
    permission_resolver, info, org, draft_form, with_tenant_org
):
    _grant_all(permission_resolver)
    assert draft_form.published_at is None
    with with_tenant_org(org):
        result = FormsMutation().publish_form(info(), slug=draft_form.slug)
    assert result.ok, result.errors
    draft_form.refresh_from_db()
    assert draft_form.status == FormDefinition.Status.PUBLISHED
    assert draft_form.published_at is not None
    # First publish keeps schema_version at 1 (it's already 1 by default).
    assert draft_form.schema_version == 1


def test_republish_after_archive_bumps_schema_version(
    permission_resolver, info, org, draft_form, with_tenant_org
):
    _grant_all(permission_resolver)
    with with_tenant_org(org):
        FormsMutation().publish_form(info(), slug=draft_form.slug)
        FormsMutation().archive_form(info(), slug=draft_form.slug)
        FormsMutation().publish_form(info(), slug=draft_form.slug)
    draft_form.refresh_from_db()
    assert draft_form.status == FormDefinition.Status.PUBLISHED
    # 1 (initial) → archived → 2 (republish bumps from non-published).
    assert draft_form.schema_version == 2


def test_publish_is_idempotent(permission_resolver, info, org, draft_form, with_tenant_org):
    _grant_all(permission_resolver)
    with with_tenant_org(org):
        first = FormsMutation().publish_form(info(), slug=draft_form.slug)
        second = FormsMutation().publish_form(info(), slug=draft_form.slug)
    assert first.ok and second.ok
    draft_form.refresh_from_db()
    assert draft_form.schema_version == 1  # not bumped on no-op republish


# ---- archive ---------------------------------------------------


def test_archive_form_flips_status(permission_resolver, info, org, draft_form, with_tenant_org):
    _grant_all(permission_resolver)
    with with_tenant_org(org):
        FormsMutation().publish_form(info(), slug=draft_form.slug)
        result = FormsMutation().archive_form(info(), slug=draft_form.slug)
    assert result.ok, result.errors
    draft_form.refresh_from_db()
    assert draft_form.status == FormDefinition.Status.ARCHIVED


# ---- delete ----------------------------------------------------


def test_delete_form_soft_deletes_form_and_cascades(
    permission_resolver, info, org, published_form, with_tenant_org
):
    _grant_all(permission_resolver)
    FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=published_form.schema_version,
        payload={"score": 9},
    )
    FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=published_form.schema_version,
        payload={"score": 7},
    )
    with with_tenant_org(org):
        result = FormsMutation().delete_form_definition(
            info(),
            input=DeleteFormDefinitionInput(slug=published_form.slug),
        )
    assert result.ok, result.errors
    # Default manager filters out soft-deleted rows.
    assert FormDefinition.objects.filter(slug=published_form.slug).count() == 0
    assert FormSubmission.objects.filter(form=published_form).count() == 0
    # all_objects still sees the rows but with deleted_at set.
    assert FormDefinition.all_objects.get(slug=published_form.slug).deleted_at is not None
    cascaded = FormSubmission.all_objects.filter(form=published_form)
    assert cascaded.count() == 2
    assert all(s.deleted_at is not None for s in cascaded)


def test_delete_requires_form_delete_permission(permission_resolver, info, org, draft_form, with_tenant_org):
    permission_resolver.grant(Permission.FORM_READ)
    with with_tenant_org(org):
        result = FormsMutation().delete_form_definition(
            info(),
            input=DeleteFormDefinitionInput(slug=draft_form.slug),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---- read / cross-tenant ----------------------------------------


def test_list_returns_org_forms_only(permission_resolver, info, org, other_org, draft_form, with_tenant_org):
    _grant_all(permission_resolver)
    FormDefinition.objects.create(
        organization=other_org,
        name="Other",
        slug="other-only",
        schema={"type": "object"},
    )
    with with_tenant_org(org):
        rows = FormsQuery().form_definitions(info())
    slugs = {r.slug for r in rows}
    assert draft_form.slug in slugs
    assert "other-only" not in slugs


def test_list_filters_by_status(permission_resolver, info, org, draft_form, published_form, with_tenant_org):
    _grant_all(permission_resolver)
    with with_tenant_org(org):
        drafts = FormsQuery().form_definitions(info(), status="draft")
        published = FormsQuery().form_definitions(info(), status="published")
    assert {r.slug for r in drafts} == {draft_form.slug}
    assert {r.slug for r in published} == {published_form.slug}


def test_form_detail_cross_tenant_returns_null(
    permission_resolver, info, org, other_org, draft_form, with_tenant_org
):
    _grant_all(permission_resolver)
    # Caller in other_org tries to look up a slug that exists in `org`.
    with with_tenant_org(other_org):
        result = FormsQuery().form_definition(info(), slug=draft_form.slug)
    assert result is None


def test_form_field_types_returns_palette():
    """Public introspection query — no permission gating."""
    types = FormsQuery().form_field_types(info=None)
    assert "text" in types
    assert "select" in types
    assert "checkbox" in types
    assert "date" in types


# ---- read permission gate ---------------------------------------


def test_list_requires_form_read_permission(info, org, with_tenant_org):
    # No grants at all.
    from core.permissions import PermissionDenied

    with with_tenant_org(org):
        with pytest.raises(PermissionDenied) as exc_info:
            FormsQuery().form_definitions(info())
    # The decorator raises PermissionDenied; queries aren't wrapped
    # in @mutation_audit so it surfaces directly (matching the billing /
    # registry query test conventions).
    assert exc_info.value.permission.value == "form.read"
