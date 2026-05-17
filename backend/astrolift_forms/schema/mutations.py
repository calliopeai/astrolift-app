"""Mutations for the forms surface (#453).

* ``createFormDefinition(input)`` — create a draft form.
* ``publishForm(slug)`` — flip a draft to published, bump
  ``schema_version``, stamp ``published_at``.
* ``archiveForm(slug)`` — flip a non-archived form to archived. The
  underlying row stays alive (only soft-deleted on
  ``deleteFormDefinition``) so submissions keep resolving.
* ``deleteFormDefinition(input)`` — soft-delete the form + cascade
  soft-delete every submission.
* ``submitForm(slug, payload)`` — record a submission. Validates
  required fields against the form's JSON-Schema-shaped ``schema``
  column.
* ``updateSubmissionStatus(submissionId, status)`` — moderate a
  submission (``new`` → ``reviewed`` / ``flagged`` / ``archived``).

Every mutation gates at the resolver entry, returns a
``MutationResult`` envelope, and never raises.

Public submissions: when a FormDefinition has ``is_public=True``, the
``submitForm`` mutation will accept an anonymous submission *if the
form is published*. Otherwise the caller still needs ``form.submit``.
The shipped FE does not currently expose a public submit page outside
the authenticated app, but the data model + permission gate are in
place so the affordance can land without a backend change.
"""

from __future__ import annotations

import logging
from typing import Any

import strawberry
from django.utils import timezone
from strawberry.types import Info

from astrolift_forms.models import FormDefinition, FormSubmission
from astrolift_forms.schema.queries import form_to_type, submission_to_type
from astrolift_forms.schema.types import FormDefinitionType, FormSubmissionType
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)


_NAME_MAX = 128
_SLUG_MAX = 128
_PAYLOAD_MAX_BYTES = 256 * 1024  # 256 KiB — well above any sane form, refuses obvious abuse.
_VALID_SUBMISSION_STATUSES: frozenset[str] = frozenset({s.value for s in FormSubmission.Status})


# ---- helpers -------------------------------------------------------


def _current_user():
    """Return the request's User row or None. The audit decorator stamps
    actor_user_id from the same TenantContext, so callers don't need to
    keep two sources of truth for who acted."""
    tenant = get_current_tenant()
    if tenant is None or tenant.actor_user_id is None:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=tenant.actor_user_id).first()


def _resolve_org() -> Organization | None:
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return None
    return Organization.objects.filter(pk=tenant.organization_id).first()


def _validate_schema(schema: Any) -> str | None:
    """Refuse obvious junk in the schema column.

    We don't do full JSON-Schema validation here — the FE builder
    already enforces shape — but we do guard against the cases that
    would silently break the runtime renderer: non-object root,
    missing/non-object ``properties`` when present, ``required`` that
    isn't a list. Returns an error message on failure, else None.
    """
    if not isinstance(schema, dict):
        return "schema must be a JSON object"
    props = schema.get("properties")
    if props is not None and not isinstance(props, dict):
        return "schema.properties must be an object"
    required = schema.get("required")
    if required is not None and not isinstance(required, list):
        return "schema.required must be a list"
    return None


def _validate_payload_against_schema(payload: dict, schema: dict) -> tuple[bool, str, str | None]:
    """Check the submitter's payload covers every required field.

    Returns ``(ok, message, field)``. ``field`` is the first missing
    required name on failure so the FE can put a ``setError`` on the
    specific input.
    """
    if not isinstance(payload, dict):
        return False, "payload must be a JSON object", None
    required = schema.get("required") or []
    if not isinstance(required, list):
        return True, "", None
    properties = schema.get("properties") or {}
    for name in required:
        if not isinstance(name, str):
            continue
        # An empty string / null / missing all count as "not provided"
        # for the purpose of required-field admission. Submitters can
        # still send 0 or False — those are explicit answers.
        if name not in payload:
            return False, f"missing required field: {name}", name
        value = payload[name]
        if value is None or value == "":
            return False, f"missing required field: {name}", name
        # If the schema marks the field as a list and we got an empty
        # list, treat that as "not provided" too — matches the FE
        # validation behaviour.
        field_schema = properties.get(name) or {}
        if field_schema.get("type") == "array" and isinstance(value, list) and len(value) == 0:
            return False, f"missing required field: {name}", name
    return True, "", None


def _bytes_of_payload(payload: Any) -> int:
    import json

    try:
        return len(json.dumps(payload).encode("utf-8"))
    except (TypeError, ValueError):
        return _PAYLOAD_MAX_BYTES + 1  # forces a validation failure path


# ---- inputs --------------------------------------------------------


@strawberry.input
class FormDefinitionInput:
    """Create-form payload from the new-form page (#437)."""

    name: str
    slug: str
    schema: strawberry.scalars.JSON
    description: str | None = None
    field_config: strawberry.scalars.JSON | None = None
    logic_rules: strawberry.scalars.JSON | None = None
    scoring: strawberry.scalars.JSON | None = None
    form_type: str | None = None
    is_public: bool | None = None


@strawberry.input
class FormDefinitionUpdateInput:
    """Patch-style update for an existing draft (or archived form
    being revived). All fields optional except the slug, which keys
    the row."""

    slug: str
    name: str | None = None
    schema: strawberry.scalars.JSON | None = None
    description: str | None = None
    field_config: strawberry.scalars.JSON | None = None
    logic_rules: strawberry.scalars.JSON | None = None
    scoring: strawberry.scalars.JSON | None = None
    form_type: str | None = None
    is_public: bool | None = None


@strawberry.input
class DeleteFormDefinitionInput:
    slug: str


# ---- root ----------------------------------------------------------


@strawberry.type
class FormsMutation:
    @strawberry.field
    @mutation_audit(action="form.create")
    @require_permission(Permission.FORM_CREATE)
    @tenant_scoped()
    def create_form_definition(
        self,
        info: Info,
        input: FormDefinitionInput,
    ) -> MutationResultType[FormDefinitionType]:
        name = (input.name or "").strip()
        slug = (input.slug or "").strip()
        if not name:
            return gql_failure(ErrorCode.VALIDATION.value, "name is required", field="name")
        if not slug:
            return gql_failure(ErrorCode.VALIDATION.value, "slug is required", field="slug")
        if len(name) > _NAME_MAX:
            return gql_failure(ErrorCode.VALIDATION.value, f"name too long (max {_NAME_MAX})", field="name")
        if len(slug) > _SLUG_MAX:
            return gql_failure(ErrorCode.VALIDATION.value, f"slug too long (max {_SLUG_MAX})", field="slug")

        err = _validate_schema(input.schema)
        if err is not None:
            return gql_failure(ErrorCode.VALIDATION.value, err, field="schema")

        org = _resolve_org()
        if org is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        # Pre-check on the unique constraint so the user sees a friendly
        # CONFLICT instead of a 500 from the partial index.
        if FormDefinition.objects.filter(organization=org, slug=slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                "a form with that slug already exists in this organization",
                field="slug",
            )

        actor = _current_user()
        from django.db import IntegrityError

        try:
            form = FormDefinition.objects.create(
                organization=org,
                name=name,
                slug=slug,
                description=(input.description or "").strip(),
                schema=input.schema or {},
                field_config=input.field_config or {},
                logic_rules=input.logic_rules or [],
                scoring=input.scoring or {},
                form_type=(input.form_type or "generic").strip() or "generic",
                is_public=bool(input.is_public),
                status=FormDefinition.Status.DRAFT,
                schema_version=1,
                created_by=actor,
                updated_by=actor,
            )
        except IntegrityError:
            return gql_failure(
                ErrorCode.CONFLICT.value,
                "a form with that slug already exists in this organization",
                field="slug",
            )

        return gql_success(form_to_type(form))

    @strawberry.field
    @mutation_audit(action="form.update")
    @require_permission(Permission.FORM_UPDATE)
    @tenant_scoped()
    def update_form_definition(
        self,
        info: Info,
        input: FormDefinitionUpdateInput,
    ) -> MutationResultType[FormDefinitionType]:
        slug = (input.slug or "").strip()
        if not slug:
            return gql_failure(ErrorCode.VALIDATION.value, "slug is required", field="slug")

        org = _resolve_org()
        if org is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        form = FormDefinition.objects.filter(organization=org, slug=slug).first()
        if form is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "form not found", field="slug")

        if form.status == FormDefinition.Status.ARCHIVED:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "archived forms cannot be edited; restore via publish",
                field="slug",
            )

        if input.schema is not None:
            err = _validate_schema(input.schema)
            if err is not None:
                return gql_failure(ErrorCode.VALIDATION.value, err, field="schema")
            form.schema = input.schema
        if input.name is not None:
            name = input.name.strip()
            if not name:
                return gql_failure(ErrorCode.VALIDATION.value, "name cannot be empty", field="name")
            if len(name) > _NAME_MAX:
                return gql_failure(
                    ErrorCode.VALIDATION.value, f"name too long (max {_NAME_MAX})", field="name"
                )
            form.name = name
        if input.description is not None:
            form.description = input.description.strip()
        if input.field_config is not None:
            form.field_config = input.field_config
        if input.logic_rules is not None:
            form.logic_rules = input.logic_rules
        if input.scoring is not None:
            form.scoring = input.scoring
        if input.form_type is not None:
            form.form_type = input.form_type.strip() or "generic"
        if input.is_public is not None:
            form.is_public = bool(input.is_public)

        form.updated_by = _current_user()
        form.save()
        return gql_success(form_to_type(form))

    @strawberry.field
    @mutation_audit(action="form.publish")
    @require_permission(Permission.FORM_UPDATE)
    @tenant_scoped()
    def publish_form(
        self,
        info: Info,
        slug: str,
    ) -> MutationResultType[FormDefinitionType]:
        org = _resolve_org()
        if org is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        form = FormDefinition.objects.filter(organization=org, slug=slug).first()
        if form is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "form not found", field="slug")

        if form.status == FormDefinition.Status.PUBLISHED:
            # Idempotent — re-publish is a no-op so the FE can call it
            # without first reading the current status. Returning the
            # current state keeps the UI in sync.
            return gql_success(form_to_type(form))

        # First publish stays at v1; every subsequent
        # non-published → published transition bumps the schema
        # version so historical submissions can reference the schema
        # they were captured against.
        was_first_publish = form.published_at is None
        form.status = FormDefinition.Status.PUBLISHED
        form.published_at = timezone.now()
        if not was_first_publish:
            form.schema_version = int(form.schema_version or 1) + 1
        else:
            form.schema_version = max(1, int(form.schema_version or 1))
        form.updated_by = _current_user()
        form.save()
        return gql_success(form_to_type(form))

    @strawberry.field
    @mutation_audit(action="form.archive")
    @require_permission(Permission.FORM_UPDATE)
    @tenant_scoped()
    def archive_form(
        self,
        info: Info,
        slug: str,
    ) -> MutationResultType[FormDefinitionType]:
        org = _resolve_org()
        if org is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        form = FormDefinition.objects.filter(organization=org, slug=slug).first()
        if form is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "form not found", field="slug")

        if form.status == FormDefinition.Status.ARCHIVED:
            return gql_success(form_to_type(form))

        form.status = FormDefinition.Status.ARCHIVED
        form.updated_by = _current_user()
        form.save()
        return gql_success(form_to_type(form))

    @strawberry.field
    @mutation_audit(action="form.delete")
    @require_permission(Permission.FORM_DELETE)
    @tenant_scoped()
    def delete_form_definition(
        self,
        info: Info,
        input: DeleteFormDefinitionInput,
    ) -> MutationResultType[FormDefinitionType]:
        slug = (input.slug or "").strip()
        if not slug:
            return gql_failure(ErrorCode.VALIDATION.value, "slug is required", field="slug")

        org = _resolve_org()
        if org is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        form = FormDefinition.objects.filter(organization=org, slug=slug).first()
        if form is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "form not found", field="slug")

        actor = _current_user()
        # Cascade soft-delete: every live submission for this form
        # gets the same deleted_at stamp so list queries (which filter
        # ``deleted_at__isnull=True`` by default via SoftDeleteManager)
        # stop returning them. Loop rather than ``update()`` so
        # ``soft_delete`` runs the version-bumping save path on each row.
        for sub in FormSubmission.objects.filter(form=form, deleted_at__isnull=True):
            sub.soft_delete(by=actor)
        form.soft_delete(by=actor)
        return gql_success(form_to_type(form))

    @strawberry.field
    @mutation_audit(action="form.submit")
    @tenant_scoped()
    def submit_form(
        self,
        info: Info,
        slug: str,
        payload: strawberry.scalars.JSON,
    ) -> MutationResultType[FormSubmissionType]:
        """Submit a payload against a published form.

        The permission gate here is *conditional* because of the
        public-submission case: when the form is published *and* the
        form is flagged ``is_public``, we accept the submission without
        the ``form.submit`` perm (the submitter may be authenticated
        but not a team member, or even anonymous). Otherwise we
        require ``form.submit``.

        We don't decorate with ``@require_permission`` because the
        admission rule depends on a row we have to load first. The
        gate lives inline below.
        """
        org = _resolve_org()
        if org is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        form = FormDefinition.objects.filter(organization=org, slug=slug).first()
        if form is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "form not found", field="slug")

        if form.status != FormDefinition.Status.PUBLISHED:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "form is not published",
                field="slug",
            )

        # Permission gate. Public forms skip the membership check (the
        # whole point of is_public is to accept anonymous / non-member
        # submissions); private forms still gate on form.submit.
        if not form.is_public:
            from core.permissions import PermissionDenied, check_permission

            try:
                check_permission(Permission.FORM_SUBMIT)
            except PermissionDenied as exc:
                return gql_failure(ErrorCode.PERMISSION_DENIED.value, exc.reason)

        if _bytes_of_payload(payload) > _PAYLOAD_MAX_BYTES:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"payload too large (max {_PAYLOAD_MAX_BYTES} bytes)",
                field="payload",
            )

        ok, msg, missing_field = _validate_payload_against_schema(payload or {}, form.schema or {})
        if not ok:
            return gql_failure(ErrorCode.VALIDATION.value, msg, field=missing_field or "payload")

        actor = _current_user()
        request = getattr(getattr(info, "context", None), "request", None)
        source_ip = _extract_source_ip(request)
        user_agent = _extract_user_agent(request)

        sub = FormSubmission.objects.create(
            form=form,
            organization=org,
            form_version=int(form.schema_version or 1),
            payload=payload or {},
            submitter=actor,
            submitter_email=(actor.email if actor and actor.email else ""),
            source_ip=source_ip,
            user_agent=user_agent,
            status=FormSubmission.Status.NEW,
            created_by=actor,
        )

        # Increment the denormalised counter on the form. Use F() so
        # concurrent submits don't race on the read-modify-write.
        from django.db.models import F

        FormDefinition.objects.filter(pk=form.pk).update(
            submission_count_cached=F("submission_count_cached") + 1
        )

        return gql_success(submission_to_type(sub))

    @strawberry.field
    @mutation_audit(action="form.submission.update_status")
    @require_permission(Permission.FORM_MODERATE)
    @tenant_scoped()
    def update_submission_status(
        self,
        info: Info,
        submission_id: GUID,
        status: str,
    ) -> MutationResultType[FormSubmissionType]:
        status = (status or "").strip().lower()
        if status not in _VALID_SUBMISSION_STATUSES:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"invalid status; allowed: {sorted(_VALID_SUBMISSION_STATUSES)}",
                field="status",
            )

        org = _resolve_org()
        if org is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        sub = (
            FormSubmission.objects.filter(
                guid=str(submission_id),
                organization=org,
            )
            .select_related("form", "submitter")
            .first()
        )
        if sub is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "submission not found", field="submissionId")

        sub.status = status
        sub.updated_by = _current_user()
        sub.save()
        return gql_success(submission_to_type(sub))


# ---- request helpers ------------------------------------------------


def _extract_source_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR") if hasattr(request, "META") else None
    if forwarded:
        # XFF can carry a comma-separated list of proxies; the leftmost
        # entry is the original client (per RFC 7239 convention).
        return forwarded.split(",")[0].strip() or None
    remote = request.META.get("REMOTE_ADDR") if hasattr(request, "META") else None
    return remote or None


def _extract_user_agent(request) -> str:
    if request is None:
        return ""
    if not hasattr(request, "META"):
        return ""
    return (request.META.get("HTTP_USER_AGENT") or "")[:512]
