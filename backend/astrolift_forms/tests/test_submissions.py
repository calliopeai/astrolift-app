"""Tests for FormSubmission flow + listing + moderation (#453).

Covers:

* Submit against a published form snapshots ``schema_version`` onto
  the row and increments the cached counter.
* Submit refuses non-published forms (PRECONDITION).
* Submit refuses missing required fields (VALIDATION, with the
  missing field on the error).
* Submit refuses oversized payloads (>256 KiB).
* Permission gate: private forms require ``form.submit``; public
  forms accept submissions even without the perm.
* Cross-tenant: cannot submit on another org's form.
* Listing returns newest-first, scoped to the form, and refuses
  non-org forms (returns empty rather than leaking).
* Listing filters by status.
* Status moderation flips the row's status; rejects unknown statuses.
* Status moderation requires ``form.moderate``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_forms.models import FormDefinition, FormSubmission
from astrolift_forms.schema.mutations import FormsMutation
from astrolift_forms.schema.queries import FormsQuery
from core.permissions import Permission

pytestmark = pytest.mark.django_db


def _grant(resolver, *perms):
    for p in perms:
        resolver.grant(p)


def _info_with_request(remote_ip: str = "10.0.0.7", ua: str = "pytest/1.0"):
    request = SimpleNamespace(
        META={
            "REMOTE_ADDR": remote_ip,
            "HTTP_USER_AGENT": ua,
        }
    )
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


# ---- submit -----------------------------------------------------


def test_submit_records_payload_and_bumps_counter(permission_resolver, org, published_form, with_tenant_org):
    _grant(permission_resolver, Permission.FORM_SUBMIT)
    with with_tenant_org(org):
        result = FormsMutation().submit_form(
            _info_with_request(),
            slug=published_form.slug,
            payload={"score": 9, "comment": "great"},
        )
    assert result.ok, result.errors
    rows = FormSubmission.objects.filter(form=published_form)
    assert rows.count() == 1
    row = rows.get()
    assert row.payload == {"score": 9, "comment": "great"}
    # form_version snapshot reads from FormDefinition.schema_version.
    assert row.form_version == published_form.schema_version
    assert row.source_ip == "10.0.0.7"
    assert row.user_agent == "pytest/1.0"
    # Cached counter incremented.
    published_form.refresh_from_db()
    assert published_form.submission_count_cached == 1


def test_submit_against_draft_form_refused(permission_resolver, org, draft_form, with_tenant_org):
    _grant(permission_resolver, Permission.FORM_SUBMIT)
    with with_tenant_org(org):
        result = FormsMutation().submit_form(
            _info_with_request(),
            slug=draft_form.slug,
            payload={"name": "x"},
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_submit_missing_required_field_is_validation_error(
    permission_resolver, org, published_form, with_tenant_org
):
    _grant(permission_resolver, Permission.FORM_SUBMIT)
    with with_tenant_org(org):
        result = FormsMutation().submit_form(
            _info_with_request(),
            slug=published_form.slug,
            # 'score' is required by the published_form fixture.
            payload={"comment": "no score"},
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "score"


def test_submit_empty_required_value_rejected(permission_resolver, org, published_form, with_tenant_org):
    _grant(permission_resolver, Permission.FORM_SUBMIT)
    with with_tenant_org(org):
        result = FormsMutation().submit_form(
            _info_with_request(),
            slug=published_form.slug,
            payload={"score": ""},  # empty string treated as missing
        )
    assert not result.ok
    assert result.errors[0].field == "score"


def test_submit_zero_or_false_explicitly_accepted(permission_resolver, org, with_tenant_org):
    """A submitter answering 0 / False / [] on a *non-required* field
    is an explicit answer, not a missing one. We snapshot the value."""
    _grant(permission_resolver, Permission.FORM_SUBMIT)
    form = FormDefinition.objects.create(
        organization=org,
        name="Quiz",
        slug="quiz",
        schema={
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "subscribe": {"type": "boolean"},
            },
            "required": ["score"],
        },
        status=FormDefinition.Status.PUBLISHED,
    )
    with with_tenant_org(org):
        result = FormsMutation().submit_form(
            _info_with_request(),
            slug=form.slug,
            payload={"score": 0, "subscribe": False},
        )
    assert result.ok, result.errors
    row = FormSubmission.objects.get(form=form)
    assert row.payload == {"score": 0, "subscribe": False}


def test_submit_oversized_payload_rejected(permission_resolver, org, with_tenant_org):
    _grant(permission_resolver, Permission.FORM_SUBMIT)
    form = FormDefinition.objects.create(
        organization=org,
        name="Big",
        slug="big",
        schema={"type": "object", "properties": {"blob": {"type": "string"}}},
        status=FormDefinition.Status.PUBLISHED,
    )
    big = "x" * (300 * 1024)  # ~300 KiB string — over the 256 KiB cap.
    with with_tenant_org(org):
        result = FormsMutation().submit_form(
            _info_with_request(),
            slug=form.slug,
            payload={"blob": big},
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "too large" in result.errors[0].message


def test_submit_xff_header_captured_first_hop(permission_resolver, org, published_form, with_tenant_org):
    _grant(permission_resolver, Permission.FORM_SUBMIT)
    request = SimpleNamespace(
        META={
            "HTTP_X_FORWARDED_FOR": "203.0.113.5, 10.0.0.1, 10.0.0.2",
            "REMOTE_ADDR": "10.0.0.2",
            "HTTP_USER_AGENT": "ua",
        }
    )
    info = SimpleNamespace(context=SimpleNamespace(user=None, request=request))
    with with_tenant_org(org):
        result = FormsMutation().submit_form(
            info,
            slug=published_form.slug,
            payload={"score": 8},
        )
    assert result.ok, result.errors
    row = FormSubmission.objects.get(form=published_form)
    assert row.source_ip == "203.0.113.5"


# ---- permission gate --------------------------------------------


def test_submit_private_form_requires_form_submit_permission(
    permission_resolver, org, published_form, with_tenant_org
):
    # No grants.
    with with_tenant_org(org):
        result = FormsMutation().submit_form(
            _info_with_request(),
            slug=published_form.slug,
            payload={"score": 9},
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_submit_public_form_accepts_without_permission(permission_resolver, org, with_tenant_org):
    # Public form: ``is_public=True`` lifts the form.submit gate.
    form = FormDefinition.objects.create(
        organization=org,
        name="Public",
        slug="public-form",
        schema={"type": "object", "properties": {"q": {"type": "string"}}},
        status=FormDefinition.Status.PUBLISHED,
        is_public=True,
    )
    # No FORM_SUBMIT grant.
    with with_tenant_org(org):
        result = FormsMutation().submit_form(
            _info_with_request(),
            slug=form.slug,
            payload={"q": "answer"},
        )
    assert result.ok, result.errors


# ---- cross-tenant -----------------------------------------------


def test_submit_other_orgs_form_returns_not_found(
    permission_resolver, org, other_org, published_form, with_tenant_org
):
    _grant(permission_resolver, Permission.FORM_SUBMIT)
    # Caller is in other_org; the form is in org.
    with with_tenant_org(other_org):
        result = FormsMutation().submit_form(
            _info_with_request(),
            slug=published_form.slug,
            payload={"score": 9},
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- listing ----------------------------------------------------


def test_form_submissions_returns_newest_first(permission_resolver, org, published_form, with_tenant_org):
    from django.utils import timezone

    _grant(permission_resolver, Permission.FORM_READ, Permission.FORM_SUBMIT)
    earlier = FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=published_form.schema_version,
        payload={"score": 1},
    )
    later = FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=published_form.schema_version,
        payload={"score": 2},
    )
    # ``submitted_at`` is auto_now_add; rather than fight Django on that,
    # rely on insert order — Postgres orders by ``submitted_at`` DESC and
    # the second insert lands after the first.
    assert later.submitted_at >= earlier.submitted_at

    with with_tenant_org(org):
        rows = FormsQuery().form_submissions(_info_with_request(), slug=published_form.slug)
    assert [r.payload["score"] for r in rows] == [2, 1]
    # And the now-imported timezone gets used by the cap-check below.
    assert all(r.submitted_at <= timezone.now() for r in rows)


def test_form_submissions_filters_by_status(permission_resolver, org, published_form, with_tenant_org):
    _grant(permission_resolver, Permission.FORM_READ)
    FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=1,
        payload={"score": 1},
        status=FormSubmission.Status.NEW,
    )
    FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=1,
        payload={"score": 2},
        status=FormSubmission.Status.FLAGGED,
    )
    with with_tenant_org(org):
        flagged = FormsQuery().form_submissions(
            _info_with_request(), slug=published_form.slug, status="flagged"
        )
    assert [r.status for r in flagged] == ["flagged"]


def test_form_submissions_cross_tenant_returns_empty(
    permission_resolver, org, other_org, published_form, with_tenant_org
):
    _grant(permission_resolver, Permission.FORM_READ)
    FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=1,
        payload={"score": 1},
    )
    with with_tenant_org(other_org):
        rows = FormsQuery().form_submissions(_info_with_request(), slug=published_form.slug)
    assert rows == []


def test_form_submissions_requires_form_read_permission(
    permission_resolver, org, published_form, with_tenant_org
):
    # No grants.
    from core.permissions import PermissionDenied

    with with_tenant_org(org):
        with pytest.raises(PermissionDenied) as exc:
            FormsQuery().form_submissions(_info_with_request(), slug=published_form.slug)
    assert exc.value.permission.value == "form.read"


def test_form_submissions_excludes_soft_deleted(permission_resolver, org, published_form, with_tenant_org):
    _grant(permission_resolver, Permission.FORM_READ)
    live = FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=1,
        payload={"score": 1},
    )
    gone = FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=1,
        payload={"score": 2},
    )
    gone.soft_delete()
    with with_tenant_org(org):
        rows = FormsQuery().form_submissions(_info_with_request(), slug=published_form.slug)
    assert {r.id for r in rows} == {str(live.guid)}


# ---- moderation -------------------------------------------------


def test_update_submission_status_flips_status(permission_resolver, org, published_form, with_tenant_org):
    _grant(permission_resolver, Permission.FORM_MODERATE)
    sub = FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=1,
        payload={"score": 9},
    )
    with with_tenant_org(org):
        result = FormsMutation().update_submission_status(
            _info_with_request(),
            submission_id=str(sub.guid),
            status="reviewed",
        )
    assert result.ok, result.errors
    sub.refresh_from_db()
    assert sub.status == "reviewed"


def test_update_submission_status_rejects_unknown_status(
    permission_resolver, org, published_form, with_tenant_org
):
    _grant(permission_resolver, Permission.FORM_MODERATE)
    sub = FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=1,
        payload={"score": 9},
    )
    with with_tenant_org(org):
        result = FormsMutation().update_submission_status(
            _info_with_request(),
            submission_id=str(sub.guid),
            status="bogus",
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "status"


def test_update_submission_status_requires_form_moderate(
    permission_resolver, org, published_form, with_tenant_org
):
    permission_resolver.grant(Permission.FORM_READ)
    # No FORM_MODERATE grant.
    sub = FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=1,
        payload={"score": 9},
    )
    with with_tenant_org(org):
        result = FormsMutation().update_submission_status(
            _info_with_request(),
            submission_id=str(sub.guid),
            status="reviewed",
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_update_submission_status_other_orgs_submission_not_found(
    permission_resolver, org, other_org, published_form, with_tenant_org
):
    _grant(permission_resolver, Permission.FORM_MODERATE)
    sub = FormSubmission.objects.create(
        form=published_form,
        organization=org,
        form_version=1,
        payload={"score": 9},
    )
    with with_tenant_org(other_org):
        result = FormsMutation().update_submission_status(
            _info_with_request(),
            submission_id=str(sub.guid),
            status="reviewed",
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
