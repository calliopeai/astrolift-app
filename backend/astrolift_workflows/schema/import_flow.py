"""Visual-flow import GraphQL surface (#984/#985/#986, spec 41 §6 corollary).

``importWorkflowFlow(format, payload, preview)`` — a single mutation that maps
a popular visual builder's export (Langflow, Flowise, …) onto Astrolift's
native workflow representation via the importer framework
(:mod:`workflows.importers`), then feeds the **same** structured manifest the
native TOML path uses into the **same** create routine
(:func:`workflows.manifest.create_definition_from_manifest`).

* ``preview = true`` (default) — parse only: returns the mapped manifest +
  the structured gap report. **Nothing is persisted.**
* ``preview = false`` — additionally persists an org-scoped, disabled
  ``WorkflowDefinition`` + stages (agents bound later, like a cloned global).

``WORKFLOW_CREATE``-gated + ``@tenant_scoped``; org scoping is real (the new
definition is owned by the caller's active org), deny-by-default.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_workflows.import_scopes import workflow_import_org_scope
from astrolift_workflows.schema.manifest import (
    WorkflowManifestDefinitionType,
    WorkflowManifestStageType,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.schema.common import MutationResult, ValidationError
from core.tenancy import get_current_tenant
from workflows.importers import FlowImportError, get_importer
from workflows.importers.base import FlowImportResult

JSON = strawberry.scalars.JSON


@strawberry.type
class ImportGapType:
    """One source construct that did not translate cleanly."""

    code: str
    message: str
    node_id: str | None
    node_type: str | None
    severity: str


@strawberry.type
class ImportWorkflowFlowResult(MutationResult):
    """Mapped manifest + gap report; ``created_slug`` set only on a non-preview
    create."""

    preview: bool = True
    created_slug: str | None = None
    definition: WorkflowManifestDefinitionType | None = None
    stages: list[WorkflowManifestStageType] = strawberry.field(default_factory=list)
    gaps: list[ImportGapType] = strawberry.field(default_factory=list)


def _failure(field: str, message: str, *, preview: bool) -> ImportWorkflowFlowResult:
    return ImportWorkflowFlowResult(
        ok=False,
        preview=preview,
        errors=[ValidationError(field=field, messages=[message])],
    )


def _stage_types(result: FlowImportResult) -> list[WorkflowManifestStageType]:
    return [
        WorkflowManifestStageType(
            order=s.order,
            kind=s.kind,
            role=s.role,
            agent=s.agent,
            workflow=s.workflow,
            environment_spec_slug=s.environment_spec_slug,
            skills=list(s.skills),
            on_failure=s.on_failure,
            max_attempts=s.max_attempts,
            back_edge=s.back_edge,
            timeout=s.timeout,
            fan_out=str(s.fan_out),
            prompt=s.prompt,
            output_key=s.output_key,
            approvers=list(s.approvers),
        )
        for s in result.manifest.stages
    ]


def _gap_types(result: FlowImportResult) -> list[ImportGapType]:
    return [
        ImportGapType(
            code=g.code,
            message=g.message,
            node_id=g.node_id,
            node_type=g.node_type,
            severity=g.severity,
        )
        for g in result.gaps
    ]


@strawberry.type
class WorkflowImportMutation:
    @strawberry.mutation(
        description=(
            "Import a popular visual agent/workflow builder export (Langflow, "
            "Flowise, …) into an Astrolift WorkflowDefinition. preview=true "
            "(default) returns the mapped manifest + gap report without "
            "persisting; preview=false creates an org-scoped, disabled "
            "definition + stages."
        )
    )
    @require_permission(Permission.WORKFLOW_CREATE, scope=workflow_import_org_scope)
    @tenant_scoped()
    def import_workflow_flow(
        self, info: Info, format: str, payload: JSON, preview: bool = True
    ) -> ImportWorkflowFlowResult:
        try:
            importer = get_importer(format)
        except FlowImportError as exc:
            return _failure("format", str(exc), preview=preview)

        try:
            result = importer.import_flow(payload)
        except FlowImportError as exc:
            return _failure("payload", str(exc), preview=preview)

        manifest = result.manifest
        definition_type = WorkflowManifestDefinitionType(
            slug=manifest.definition.slug,
            name=manifest.definition.name,
            pattern=manifest.definition.pattern,
            description=manifest.definition.description,
        )
        stages = _stage_types(result)
        gaps = _gap_types(result)

        if preview:
            return ImportWorkflowFlowResult(
                ok=True,
                preview=True,
                definition=definition_type,
                stages=stages,
                gaps=gaps,
            )

        # Non-preview: persist into the caller's active org (deny-by-default —
        # the decorator only asserts a context exists; this is the real scope).
        from astrolift_identity.models import Organization
        from workflows.manifest import create_definition_from_manifest

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        org = Organization.objects.filter(pk=org_pk, deleted_at__isnull=True).first() if org_pk else None
        if org is None:
            return _failure("organization", "no active organization in context", preview=False)

        definition = create_definition_from_manifest(manifest, organization=org, created_by=info.context.user)
        # The persisted slug may have been uniquified on collision.
        definition_type.slug = definition.slug
        return ImportWorkflowFlowResult(
            ok=True,
            preview=False,
            created_slug=definition.slug,
            definition=definition_type,
            stages=stages,
            gaps=gaps,
        )
