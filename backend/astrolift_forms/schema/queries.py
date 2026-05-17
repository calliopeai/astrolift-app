"""Read-only queries for the forms surface (#453).

Three queries the FE bundle from #437 already calls:

* ``formDefinitions(status?)`` — list org forms, optionally filtered by
  status. Sorted newest-edit first.
* ``formDefinition(slug)`` — single form by slug, scoped to the org.
* ``formSubmissions(slug, status?)`` — submission list for a form,
  newest-first. Capped server-side at 500 so the client-side paginated
  table in ``submissions-tab.tsx`` stays responsive.
* ``formFieldTypes`` — palette enum the visual builder reads.

All resolvers gate on ``Permission.FORM_READ`` and tenant-scope to
the request org. The submission list is double-scoped (org via tenant,
then slug-resolved form) so a slug guess from a member of another org
returns an empty list rather than someone else's submissions.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_forms.models import FormDefinition, FormSubmission
from astrolift_forms.schema.types import FormDefinitionType, FormSubmissionType
from astrolift_graphql import GUID
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

# Catalog of widget types the visual builder palette renders. Kept
# in lock-step with ``frontend/components/forms/field-registry.tsx``
# — the FE consumes the resolver output as the source of available
# field kinds so we never ship a builder option without a renderer.
FORM_FIELD_TYPES: tuple[str, ...] = (
    "text",
    "textarea",
    "number",
    "email",
    "url",
    "date",
    "datetime",
    "select",
    "multi_select",
    "radio",
    "checkbox",
    "boolean",
    "file",
    "rating",
    "signature",
    "section_header",
    "text_block",
    "page_break",
    "image",
)


def _user_display(user) -> str:
    """Best-effort label for a User row. Mirrors the convention in the
    billing/identity resolvers so labels read the same across surfaces:
    full name → email → username → ``"system"``."""
    if user is None:
        return "system"
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    full = (first + " " + last).strip()
    if full:
        return full
    return (
        (getattr(user, "email", "") or "").strip()
        or (getattr(user, "username", "") or "").strip()
        or "system"
    )


def form_to_type(form: FormDefinition) -> FormDefinitionType:
    return FormDefinitionType(
        id=GUID(str(form.guid)),
        slug=form.slug,
        name=form.name,
        description=form.description or "",
        schema=form.schema or {},
        field_config=form.field_config or {},
        logic_rules=form.logic_rules or [],
        scoring=form.scoring or {},
        status=form.status,
        form_type=form.form_type or "generic",
        is_public=form.is_public,
        version=form.schema_version,
        submission_count=int(form.submission_count_cached or 0),
        published_at=form.published_at,
        created_at=form.created_at,
        updated_at=form.updated_at,
        created_by_username=_user_display(form.created_by) if form.created_by_id else None,
        updated_by_username=_user_display(form.updated_by) if form.updated_by_id else None,
    )


def submission_to_type(sub: FormSubmission) -> FormSubmissionType:
    return FormSubmissionType(
        id=GUID(str(sub.guid)),
        form_slug=sub.form.slug,
        form_name=sub.form.name,
        form_version=int(sub.form_version),
        submitted_at=sub.submitted_at,
        created_at=sub.created_at,
        submitter_display_name=_user_display(sub.submitter) if sub.submitter_id else "anonymous",
        submitter_email=sub.submitter_email or "",
        payload=sub.payload or {},
        source_ip=str(sub.source_ip) if sub.source_ip else None,
        user_agent=sub.user_agent or "",
        status=sub.status,
    )


_LIST_LIMIT = 200
_SUBMISSIONS_LIMIT = 500


@strawberry.type
class FormsQuery:
    @strawberry.field
    @require_permission(Permission.FORM_READ)
    @tenant_scoped()
    def form_definitions(
        self,
        info: Info,
        status: str | None = None,
    ) -> list[FormDefinitionType]:
        tenant = get_current_tenant()
        qs = FormDefinition.objects.filter(organization_id=tenant.organization_id)
        if status:
            qs = qs.filter(status=status)
        qs = qs.select_related("created_by", "updated_by").order_by("-updated_at")[:_LIST_LIMIT]
        return [form_to_type(f) for f in qs]

    @strawberry.field
    @require_permission(Permission.FORM_READ)
    @tenant_scoped()
    def form_definition(
        self,
        info: Info,
        slug: str,
    ) -> FormDefinitionType | None:
        tenant = get_current_tenant()
        form = (
            FormDefinition.objects.filter(
                organization_id=tenant.organization_id,
                slug=slug,
            )
            .select_related("created_by", "updated_by")
            .first()
        )
        if form is None:
            return None
        return form_to_type(form)

    @strawberry.field
    @require_permission(Permission.FORM_READ)
    @tenant_scoped()
    def form_submissions(
        self,
        info: Info,
        slug: str,
        status: str | None = None,
    ) -> list[FormSubmissionType]:
        tenant = get_current_tenant()
        form = FormDefinition.objects.filter(
            organization_id=tenant.organization_id,
            slug=slug,
        ).first()
        if form is None:
            return []
        qs = FormSubmission.objects.filter(
            form=form,
            organization_id=tenant.organization_id,
        ).select_related("form", "submitter")
        if status:
            qs = qs.filter(status=status)
        qs = qs.order_by("-submitted_at")[:_SUBMISSIONS_LIMIT]
        return [submission_to_type(s) for s in qs]

    @strawberry.field
    def form_field_types(self, info: Info) -> list[str]:
        """Palette enum the builder UI reads. Public on the schema
        because the list is metadata, not tenant data — knowing which
        widget kinds the builder offers does not leak anything."""
        return list(FORM_FIELD_TYPES)
