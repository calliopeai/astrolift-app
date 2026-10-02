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
from astrolift_workflows.import_scopes import (
    import_definition_owner_scope,
    manifest_destination_scope,
    workflow_manifest_import_scope,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, check_permission, require_permission
from core.schema.common import MutationResult, ValidationError
from core.tenancy import get_current_tenant
from workflows.manifest import (
    ParsedWorkflowManifest,
    definition_to_manifest,
    emit_workflow_manifest,
    parse_workflow_manifest,
)
from workflows.models import WorkflowDefinition
from workflows.scopes import definition_scope_by_slug


@strawberry.type
class WorkflowManifestStageType:
    order: int
    kind: str
    role: str
    agent: str | None
    workflow: str | None
    environment_spec_slug: str | None
    skills: list[str]
    on_failure: str
    max_attempts: int
    timeout: int
    # Tri-state rendered as a string: "0" (none), "N" (static), "dynamic".
    fan_out: str
    prompt: str | None
    output_key: str | None
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
                workflow=s.workflow,
                environment_spec_slug=s.environment_spec_slug,
                skills=list(s.skills),
                on_failure=s.on_failure,
                max_attempts=s.max_attempts,
                timeout=s.timeout,
                fan_out=str(s.fan_out),
                prompt=s.prompt,
                output_key=s.output_key,
                approvers=list(s.approvers),
            )
            for s in parsed.stages
        ],
    )


@strawberry.type
class WorkflowManifestQuery:
    @strawberry.field
    @require_permission(Permission.WORKFLOW_READ, any_scope=True)
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
    @require_permission(Permission.WORKFLOW_READ, scope=definition_scope_by_slug("definition_slug"))
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
            # Live rows only: a deleted same-slug definition is not the one the
            # permission scope was resolved from.
            visible = WorkflowDefinition.visible_to_org(org_pk).filter(
                slug=definition_slug, deleted_at__isnull=True
            )
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
    it is uniquified within the org on collision.

    ``mode`` / ``repointed_slugs`` are populated only by ``replace=true``
    (#1822): ``mode`` is one of ``"created"``, ``"updated_in_place"`` or
    ``"versioned"`` (see ``workflows.manifest.ReplaceOutcome``). A
    ``replace=true`` call that would break a configured Workflow's
    ``stage_bindings`` comes back ``ok=false`` with one error per blocked
    Workflow (field ``workflow.<slug>``) and persists nothing.
    """

    created_slug: str | None = None
    manifest: WorkflowManifestPreviewType | None = None
    mode: str | None = None
    repointed_slugs: list[str] = strawberry.field(default_factory=list)


@strawberry.type
class WorkflowManifestMutation:
    @strawberry.mutation(
        description=(
            "Import a workflow manifest TOML. preview=true (default) returns "
            "the parsed shape without persisting; preview=false creates a "
            "disabled, org-scoped WorkflowDefinition + stages in the caller's "
            "org and returns the (possibly uniquified) slug. replace=true "
            "instead upserts the org's own definition sharing the manifest's "
            "slug: in place when the stage kinds are unchanged (configured "
            "Workflows, bindings and schedules all keep working untouched), "
            "otherwise as a new version with every configured Workflow "
            "repointed to it, or a clear refusal when a repoint would break "
            "one's bindings."
        )
    )
    @require_permission(Permission.WORKFLOW_CREATE, scope=workflow_manifest_import_scope)
    @tenant_scoped()
    def import_workflow_manifest(
        self,
        info: Info,
        toml: str,
        preview: bool = True,
        replace: bool = False,
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

        from astrolift_workflows.schema.mutations import _resolve_caller_org

        org, err = _resolve_caller_org(org_id)
        if err is not None:
            return ImportWorkflowManifestResult(ok=err.ok, errors=err.errors)

        if replace:
            return _import_replace(info, parsed, org)

        from django.db import transaction

        from workflows.manifest import create_definition_from_manifest

        with transaction.atomic():
            definition = create_definition_from_manifest(
                parsed, organization=org, created_by=info.context.user
            )

        manifest_type = _preview_type(parsed)
        # Report the slug that actually persisted (uniquified on collision).
        manifest_type.definition.slug = definition.slug
        return ImportWorkflowManifestResult(ok=True, created_slug=definition.slug, manifest=manifest_type)


def _import_replace(info: Info, parsed: ParsedWorkflowManifest, org) -> ImportWorkflowManifestResult:
    """``importWorkflowManifest(replace: true)`` (#1822): upsert the org's
    own definition sharing ``parsed.definition.slug``. See
    ``workflows.manifest.replace_definition_from_manifest`` for the
    in-place / versioned / blocked decision.

    Gated the same as the sibling definition-write mutations: a
    source-managed or platform-global definition refuses
    (``_definition_write_error``, the same gate ``updateWorkflowDefinition``
    uses), and repointing configured Workflows requires the same
    ``WORKFLOW_UPDATE`` the caller would need to edit that definition
    directly: checked once, at the definition's own scope, since every
    configured Workflow being repointed shares that scope by construction.
    """
    from django.db import transaction

    from workflows.manifest import (
        ReplaceOutcome,
        create_definition_from_manifest,
        replace_definition_from_manifest,
    )
    from workflows.schema.mutations import _definition_write_error

    user = info.context.user
    with transaction.atomic():
        existing = (
            WorkflowDefinition.objects.select_for_update(of=("self",))
            .filter(organization=org, slug=parsed.definition.slug, deleted_at__isnull=True)
            .select_related("project__team")
            .first()
        )
        if existing is not None:
            # Freeze the shape before deciding whether this writes a project
            # recipe or creates a new organization version.
            list(
                existing.stages.select_for_update()
                .filter(deleted_at__isnull=True)
                .values_list("pk", flat=True)
            )
            write_err = _definition_write_error(user, existing)
            if write_err is not None:
                return ImportWorkflowManifestResult(
                    ok=False, errors=[ValidationError(field=write_err[0], messages=[write_err[1]])]
                )
            check_permission(
                Permission.WORKFLOW_UPDATE,
                scope=import_definition_owner_scope(existing, permission=Permission.WORKFLOW_UPDATE),
            )
        check_permission(Permission.WORKFLOW_CREATE, scope=manifest_destination_scope(parsed, existing))
        if existing is None:
            # A concurrent new same-slug row must never become an unchecked
            # replacement target; normal create only uniquifies its slug.
            outcome = ReplaceOutcome(
                definition=create_definition_from_manifest(parsed, organization=org, created_by=user),
                mode="created",
            )
        else:
            outcome = replace_definition_from_manifest(parsed, organization=org, created_by=user)

    if outcome.mode == "blocked":
        errors = [
            ValidationError(field=f"workflow.{slug}", messages=messages)
            for slug, messages in outcome.blocked_errors.items()
        ]
        return ImportWorkflowManifestResult(ok=False, errors=errors)

    manifest_type = _preview_type(parsed)
    manifest_type.definition.slug = outcome.definition.slug
    return ImportWorkflowManifestResult(
        ok=True,
        created_slug=outcome.definition.slug,
        manifest=manifest_type,
        mode=outcome.mode,
        repointed_slugs=outcome.repointed_slugs,
    )
