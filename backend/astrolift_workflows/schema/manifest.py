"""Workflow manifest import/export GraphQL (spec 40 §5.4).

Two read surfaces for the builder's Code view (§5.1) plus the write path:

* ``previewWorkflowManifest(toml)`` — parse a TOML string into its
  structured form for preview/validate. **No persistence.** Malformed
  input comes back as ``ok = false`` with a structured error (path + line
  + column), never a GraphQL error.
* ``exportWorkflowManifest(definitionSlug)`` — emit the canonical TOML for
  a visible ``WorkflowDefinition`` (the org's own UNION platform-global,
  spec 40 §2.1).
* ``importWorkflowManifest(toml, preview)`` — the native-TOML create
  surface (#970/#972): ``preview = true`` mirrors the preview query;
  ``preview = false`` persists via the same
  :func:`workflows.manifest.create_definition_from_manifest` routine the
  visual-flow importers use, always into the caller's org (never global).

Reads are ``WORKFLOW_READ``-gated, the import is ``WORKFLOW_CREATE``-gated;
all are ``@tenant_scoped``.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_manifest.parser import ManifestError
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.schema.common import MutationResult, ValidationError
from core.tenancy import get_current_tenant
from workflows.manifest import (
    ParsedWorkflowManifest,
    definition_to_manifest,
    emit_workflow_manifest,
    parse_workflow_manifest,
)
from workflows.models import WorkflowDefinition


@strawberry.type
class WorkflowManifestStageType:
    order: int
    kind: str
    role: str
    agent: str | None
    skills: list[str]
    on_failure: str
    timeout: int
    # Tri-state rendered as a string: "0" (none), "N" (static), "dynamic".
    fan_out: str
    prompt: str | None
    approvers: list[str]


@strawberry.type
class WorkflowManifestDefinitionType:
    slug: str
    name: str
    pattern: str
    description: str


@strawberry.type
class WorkflowManifestPreviewType:
    """Parsed-but-not-persisted manifest, or a structured parse error."""

    ok: bool
    error: str | None = None
    error_path: str | None = None
    error_line: int | None = None
    error_column: int | None = None
    definition: WorkflowManifestDefinitionType | None = None
    stages: list[WorkflowManifestStageType] = strawberry.field(default_factory=list)


@strawberry.type
class WorkflowManifestExportType:
    """Emitted TOML for a definition, or a structured error."""

    ok: bool
    toml: str | None = None
    error: str | None = None


def _preview_error(exc: ManifestError) -> WorkflowManifestPreviewType:
    return WorkflowManifestPreviewType(
        ok=False,
        error=str(exc),
        error_path=exc.path or None,
        error_line=exc.line,
        error_column=exc.column,
    )


def _preview_type(parsed: ParsedWorkflowManifest) -> WorkflowManifestPreviewType:
    return WorkflowManifestPreviewType(
        ok=True,
        definition=WorkflowManifestDefinitionType(
            slug=parsed.definition.slug,
            name=parsed.definition.name,
            pattern=parsed.definition.pattern,
            description=parsed.definition.description,
        ),
        stages=[
            WorkflowManifestStageType(
                order=s.order,
                kind=s.kind,
                role=s.role,
                agent=s.agent,
                skills=list(s.skills),
                on_failure=s.on_failure,
                timeout=s.timeout,
                fan_out=str(s.fan_out),
                prompt=s.prompt,
                approvers=list(s.approvers),
            )
            for s in parsed.stages
        ],
    )


@strawberry.type
class WorkflowManifestQuery:
    @strawberry.field
    @require_permission(Permission.WORKFLOW_READ)
    @tenant_scoped()
    def preview_workflow_manifest(self, info: Info, toml: str) -> WorkflowManifestPreviewType:
        """Parse a workflow manifest TOML into its structured preview.

        Pure validation — nothing is persisted. Returns ``ok = false`` with
        a structured error (message + dotted path + source line/column) when
        the TOML is malformed, so the Code view can underline the offending
        row."""
        try:
            parsed = parse_workflow_manifest(toml)
        except ManifestError as exc:
            return _preview_error(exc)

        return _preview_type(parsed)

    @strawberry.field
    @require_permission(Permission.WORKFLOW_READ)
    @tenant_scoped()
    def export_workflow_manifest(self, info: Info, definition_slug: str) -> WorkflowManifestExportType:
        """Emit the canonical TOML for a visible workflow definition.

        Scoped to the caller's org UNION platform-global templates (spec 40
        §2.1); an org-owned definition wins over a global with the same
        slug. Returns ``ok = false`` when no visible definition matches."""
        user = info.context.user
        if getattr(user, "is_superuser", False):
            definition = WorkflowDefinition.objects.filter(slug=definition_slug).first()
        else:
            tenant = get_current_tenant()
            org_pk = tenant.organization_id if tenant else None
            visible = WorkflowDefinition.visible_to_org(org_pk).filter(slug=definition_slug)
            definition = visible.filter(organization_id=org_pk).first() or visible.first()

        if definition is None:
            return WorkflowManifestExportType(
                ok=False, error=f"no visible workflow definition with slug {definition_slug!r}"
            )

        toml = emit_workflow_manifest(definition_to_manifest(definition))
        return WorkflowManifestExportType(ok=True, toml=toml)


@strawberry.type
class ImportWorkflowManifestResult(MutationResult):
    """Parsed manifest (or its structured parse error) + the created slug
    when persisting. ``created_slug`` may differ from the manifest's slug —
    it is uniquified within the org on collision."""

    created_slug: str | None = None
    manifest: WorkflowManifestPreviewType | None = None


@strawberry.type
class WorkflowManifestMutation:
    @strawberry.mutation(
        description=(
            "Import a workflow manifest TOML. preview=true (default) returns "
            "the parsed shape without persisting; preview=false creates a "
            "disabled, org-scoped WorkflowDefinition + stages in the caller's "
            "org and returns the (possibly uniquified) slug."
        )
    )
    @require_permission(Permission.WORKFLOW_CREATE)
    @tenant_scoped()
    def import_workflow_manifest(
        self,
        info: Info,
        toml: str,
        preview: bool = True,
        org_id: strawberry.ID | None = None,
    ) -> ImportWorkflowManifestResult:
        try:
            parsed = parse_workflow_manifest(toml)
        except ManifestError as exc:
            return ImportWorkflowManifestResult(
                ok=False,
                errors=[ValidationError(field="toml", messages=[str(exc)])],
                manifest=_preview_error(exc),
            )

        if preview:
            return ImportWorkflowManifestResult(ok=True, manifest=_preview_type(parsed))

        from django.db import transaction

        from astrolift_workflows.schema.mutations import _resolve_caller_org
        from workflows.manifest import create_definition_from_manifest

        org, err = _resolve_caller_org(org_id)
        if err is not None:
            return ImportWorkflowManifestResult(ok=err.ok, errors=err.errors)

        with transaction.atomic():
            definition = create_definition_from_manifest(
                parsed, organization=org, created_by=info.context.user
            )

        manifest_type = _preview_type(parsed)
        # Report the slug that actually persisted (uniquified on collision).
        manifest_type.definition.slug = definition.slug
        return ImportWorkflowManifestResult(ok=True, created_slug=definition.slug, manifest=manifest_type)
