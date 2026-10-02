from __future__ import annotations

import strawberry
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Q
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.operation_context import agent_region_operation, instance_operation
from astrolift_identity.step_up import requires_elevation
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    check_permission,
    is_platform_operator,
    require_permission,
    require_platform_operator,
)
from core.schema.common import MutationResult
from core.schema.common import ValidationError as GQLValidationError
from core.tenancy import get_current_tenant
from workflows.models import WorkflowDefinition, WorkflowInstance, WorkflowStage
from workflows.schema.reviewed_start_types import (
    StartWorkflowDefinitionInput,
    WorkflowDefinitionStartType,
    start_to_type,
)
from workflows.schema.types import WorkflowDefinitionType, WorkflowStageType
from workflows.scopes import (
    definition_scope_by_guid,
    definition_scope_by_slug,
    instance_scope_by_id,
    legacy_reviewed_definition_scope,
)


@strawberry.type
class StartWorkflowResult(MutationResult):
    instance_id: strawberry.ID | None = None


@strawberry.type
class RunWorkflowDefinitionResult(MutationResult):
    # WorkflowRun mirror pk (the executor keys stage executions to it).
    workflow_run_id: strawberry.ID | None = None
    # Temporal workflow id, for the viewer / signalling.
    temporal_workflow_id: str | None = None
    temporal_run_id: str | None = None
    request_id: str | None = None
    dispatch_status: str | None = None


@strawberry.type
class CreateWorkflowStageResult(MutationResult):
    stage: WorkflowStageType | None = None


@strawberry.type
class CloneWorkflowDefinitionResult(MutationResult):
    """Result of deep-copying a definition into the caller's org (spec 40 §2.1)."""

    slug: str | None = None
    definition: WorkflowDefinitionType | None = None


@strawberry.type
class CreateWorkflowTriggerResult(MutationResult):
    """Inbound webhook trigger for a workflow definition (#1019).
    ``signing_secret`` is the plaintext — shown ONCE, stored only as a hash."""

    slug: str | None = None
    endpoint: str | None = None
    signing_secret: str | None = None


def _require_platform_operator(user):
    """Refuse anyone but the platform operator.

    The callers write install-wide rows: a platform template that every org
    sees, or a trigger on a definition looked up across every org. Django
    staff is not the platform operator (#1978).
    """
    if not user or not user.is_authenticated:
        raise GraphQLError("Authentication required")
    require_platform_operator(user)


def _caller_org_pk():
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _definition_write_error(user, definition):
    """Deny-by-default write gate for a WorkflowDefinition (spec 40 §2.1).

    Returns ``(field, message)`` describing why ``user`` may not write
    ``definition``, or ``None`` if allowed. Platform-global (null-org)
    templates are read-only to tenants; the platform operator is exempt (the
    seeding path). Org-authored definitions are writable only by their owning
    org (the platform operator bypasses; Django staff does not, #1978). The
    org match is the actual scoping (#1042 —
    ``@tenant_scoped`` only asserts a context exists)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return ("permission", "Authentication required")
    if is_platform_operator(user):
        return None
    if definition.organization_id is None:
        return ("permission", "PERMISSION_DENIED: platform template — clone to edit")
    if definition.organization_id != _caller_org_pk():
        return ("permission", "PERMISSION_DENIED: not your organization's workflow")
    if definition.source_repo:
        location = f"{definition.source_repo}/{definition.source_path}"
        return (
            "source",
            f"SOURCE_MANAGED: edit {location} and sync the repository instead",
        )
    return None


def _resolve_clone_org(org_id):
    """Resolve ``org_id`` (Organization guid; ``None`` → the caller's active
    org) to an Organization, asserting it matches the caller's active tenant.
    Returns ``(org, None)`` on success or ``(None, failure_result)``. Real org
    scoping per #1042."""
    from astrolift_identity.models import Organization

    user_org = _caller_org_pk()
    if org_id is None:
        org = (
            Organization.objects.filter(pk=user_org, deleted_at__isnull=True).first()
            if user_org is not None
            else None
        )
        if org is None:
            return None, CloneWorkflowDefinitionResult(
                ok=False,
                errors=[GQLValidationError(field="orgId", messages=["no active organization in context"])],
            )
        return org, None
    org = Organization.objects.filter(guid=str(org_id), deleted_at__isnull=True).first()
    if org is None:
        return None, CloneWorkflowDefinitionResult(
            ok=False,
            errors=[GQLValidationError(field="orgId", messages=["organization not found"])],
        )
    if user_org is not None and org.pk != user_org:
        return None, CloneWorkflowDefinitionResult(
            ok=False,
            errors=[GQLValidationError(field="orgId", messages=["PERMISSION_DENIED: organization mismatch"])],
        )
    return org, None


def _definition_for_write(slug):
    """Resolve *slug* for a write within the caller's visible scope — the
    org's own definitions UNION platform-global, mirroring
    ``WorkflowDefinition.visible_to_org`` — preferring the org-owned row on
    slug collision; ``_definition_write_error`` then gates. A foreign org's
    slug resolves to nothing (indistinguishable from nonexistent), so no
    cross-tenant existence oracle."""
    org_pk = _caller_org_pk()
    qs = WorkflowDefinition.objects.filter(slug=slug, deleted_at__isnull=True).filter(
        Q(organization_id=org_pk) | Q(organization__isnull=True)
    )
    return qs.filter(organization_id=org_pk).first() or qs.first()


def _object_scope(obj) -> PermissionScope:
    """The narrowest scope a workflow target object reports: its app, else its
    project, else its team, else its org (#1982)."""
    for attr, kind in (
        ("registered_app_id", ScopeKind.APP),
        ("project_id", ScopeKind.PROJECT),
        ("team_id", ScopeKind.TEAM),
    ):
        value = getattr(obj, attr, None)
        if value:
            return PermissionScope(kind=kind, id=value)
    return PermissionScope(kind=ScopeKind.ORG, id=obj.organization_id)


def _unique_clone_slug(base_slug, org):
    """First free slug for ``org`` derived from ``base_slug`` (spec 40 §9 Q2)."""
    candidate = base_slug
    suffix = 0
    while WorkflowDefinition.objects.filter(
        organization=org, slug=candidate, deleted_at__isnull=True
    ).exists():
        suffix += 1
        candidate = f"{base_slug}-copy" if suffix == 1 else f"{base_slug}-copy-{suffix}"
    return candidate


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description="Start the exact reviewed definition with declared inputs and an actor-scoped requestId; retry the same request after an uncertain response."
    )
    @mutation_audit(action="workflow.definition.start")
    @require_permission(
        Permission.WORKFLOW_TRIGGER,
        scope=definition_scope_by_guid("input.definition_id"),
        operation=agent_region_operation,
    )
    @requires_elevation(action_label="Start a reviewed workflow definition")
    @tenant_scoped()
    def start_workflow_definition(
        self, info: Info, input: StartWorkflowDefinitionInput
    ) -> MutationResultType[WorkflowDefinitionStartType]:
        from django.db import IntegrityError

        from core.run_input_contract import InputContractError
        from workflows.reviewed_starts import ReviewedStartError, dispatch_start, reserve_start

        if not input.confirmed:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "Explicit confirmation of this reviewed workflow start is required",
            )
        try:
            row = reserve_start(
                definition_id=input.definition_id,
                expected_revision=input.expected_revision,
                expected_input_schema_digest=input.expected_input_schema_digest,
                request_id=input.request_id,
                inputs=input.inputs,
                user=info.context.user,
            )
            row = dispatch_start(row)
        except InputContractError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc))
        except PermissionDenied:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value, "Current authority does not permit this workflow start"
            )
        except ReviewedStartError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        except IntegrityError:
            return gql_failure(
                ErrorCode.CONFLICT.value,
                "Concurrent requestId reservation; reconcile the same request before retrying",
            )
        except Exception:
            return gql_failure(ErrorCode.INTERNAL.value, "Unable to reserve this workflow start")
        if row.dispatch_status != "submitted":
            result = gql_failure(ErrorCode.PRECONDITION.value, row.dispatch_last_error)
            result.data = start_to_type(row)
            return result
        return gql_success(start_to_type(row))

    @strawberry.mutation(description="Start a workflow for an object.")
    @require_permission(Permission.WORKFLOW_TRIGGER, scope=definition_scope_by_slug("workflow_slug"))
    @tenant_scoped()
    def start_workflow(
        self,
        info: Info,
        workflow_slug: str,
        model_label: str,
        object_id: int,
    ) -> StartWorkflowResult:
        user = info.context.user
        # Legacy forms state-machine entry (dormant, spec 40 §8) — gated
        # deny-by-default like every mutation. Definition resolution uses the
        # caller-org ∪ global scope so a foreign org's slug is not found.
        workflow = _definition_for_write(workflow_slug)
        if not workflow or not workflow.is_enabled:
            raise GraphQLError(f'Workflow "{workflow_slug}" not found or disabled')

        # Resolve the GFK target model, then scope the object to the caller's
        # org. @require_permission and @tenant_scoped above only assert a
        # capability and that *a* tenant context exists — neither FILTERS
        # (#1183). object_id is an arbitrary pk into an arbitrary model, so
        # without an explicit org constraint a WORKFLOW_TRIGGER holder could
        # start a workflow against another org's object.
        try:
            parts = model_label.split(".")
            model = apps.get_model(parts[0], parts[-1])
        except Exception as e:
            raise GraphQLError(f"Object not found: {model_label}:{object_id} — {e}") from e

        # If the target model reaches an organization, require the row to be the
        # caller's (or a platform-shared null-org row, matching
        # transition_workflow). If it has no org linkage at all we cannot prove
        # the object is the caller's, so fail closed rather than run against a
        # possibly-foreign object. A refused / out-of-scope object is
        # indistinguishable from a missing one (no cross-tenant existence
        # oracle).
        org_scoped = False
        for _org_field in ("organization", "organization_id"):
            try:
                model._meta.get_field(_org_field)
            except FieldDoesNotExist:
                continue
            org_scoped = True
            break
        if not org_scoped:
            raise GraphQLError(f"Object not found: {model_label}:{object_id}")

        # The caller's org only: an org-less row belongs to no tenant (#1965's
        # rule), and a row of another team or project in the same org needs
        # workflow.trigger at that object's own scope (#1982).
        obj = model.objects.filter(organization_id=_caller_org_pk(), pk=object_id).first()
        if obj is None:
            raise GraphQLError(f"Object not found: {model_label}:{object_id}")
        try:
            check_permission(Permission.WORKFLOW_TRIGGER, scope=_object_scope(obj))
        except PermissionDenied as exc:
            raise GraphQLError(f"Object not found: {model_label}:{object_id}") from exc

        try:
            instance = WorkflowInstance.start(workflow, obj, user, organization_id=_caller_org_pk())
            return StartWorkflowResult(ok=True, instance_id=str(instance.pk))
        except ValidationError as e:
            raise GraphQLError(str(e)) from e

    @strawberry.mutation(
        description=(
            "Compatibility entry for reviewed Definition starts. Requires exact definitionId, "
            "revision, input schema digest, caller requestId and confirmation; prefer startWorkflowDefinition."
        )
    )
    # Complete compatibility requests authorize their immutable GUID;
    # missing-proof requests retain the old scoped refusal behavior.
    @require_permission(
        Permission.WORKFLOW_TRIGGER,
        scope=legacy_reviewed_definition_scope,
        operation=agent_region_operation,
    )
    @tenant_scoped()
    def run_workflow_definition(
        self,
        info: Info,
        workflow_slug: str,
        trigger_payload: strawberry.scalars.JSON | None = None,
        definition_id: GUID | None = None,
        expected_revision: str | None = None,
        expected_input_schema_digest: str | None = None,
        request_id: str | None = None,
        confirmed: bool | None = False,
    ) -> RunWorkflowDefinitionResult:
        if (
            not all((definition_id, expected_revision, expected_input_schema_digest, request_id))
            or not confirmed
        ):
            return RunWorkflowDefinitionResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="input",
                        messages=[
                            "PRECONDITION: Upgrade to startWorkflowDefinition, or supply exact definitionId, reviewed revision/input schema digest, stable requestId and confirmed: true"
                        ],
                    )
                ],
            )
        # The legacy slug is an assertion about the exact GUID, never a resolver
        # that can substitute a newer organization copy for a reviewed template.
        definition = (
            WorkflowDefinition.visible_to_org(_caller_org_pk())
            .filter(guid=str(definition_id), deleted_at__isnull=True)
            .first()
        )
        if definition is None or definition.slug != workflow_slug:
            return RunWorkflowDefinitionResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="definition_id",
                        messages=[
                            "PRECONDITION: Exact definition not found or its slug changed; review it again"
                        ],
                    )
                ],
            )
        result = Mutation.start_workflow_definition(
            self,
            info,
            input=StartWorkflowDefinitionInput(
                definition_id=definition_id,
                expected_revision=expected_revision,
                expected_input_schema_digest=expected_input_schema_digest,
                request_id=request_id,
                inputs=trigger_payload,
                confirmed=True,
            ),
        )
        from workflows.reviewed_starts import find_start

        row = find_start(request_id) if result.data is not None else None
        return RunWorkflowDefinitionResult(
            ok=result.ok,
            errors=[
                GQLValidationError(field="input", messages=[f"{error.code}: {error.message}"])
                for error in result.errors
            ],
            workflow_run_id=str(row.execution_id) if row else None,
            temporal_workflow_id=result.data.temporal_workflow_id if result.data else None,
            temporal_run_id=result.data.temporal_run_id if result.data else None,
            request_id=request_id if row else None,
            dispatch_status=result.data.dispatch_status if result.data else None,
        )

    @strawberry.mutation(description="Transition a workflow instance to a new state.")
    @require_permission(
        Permission.WORKFLOW_TRIGGER,
        scope=instance_scope_by_id("instance_id"),
        operation=instance_operation("instance_id"),
    )
    @tenant_scoped()
    def transition_workflow(
        self,
        info: Info,
        instance_id: strawberry.ID,
        to_state: str,
        note: str = "",
    ) -> MutationResult:
        user = info.context.user
        # The caller's own org only. A legacy org-less instance
        # (pre-denormalization forms rows, spec 40 §2.3/§8) belongs to no
        # tenant, so it reads as not found like another org's (#1965).
        instance = WorkflowInstance.objects.filter(pk=instance_id, organization_id=_caller_org_pk()).first()
        if not instance:
            raise GraphQLError(f"Workflow instance {instance_id} not found")

        if instance.is_completed:
            raise GraphQLError("Workflow is already completed")

        try:
            instance.transition(to_state, user, note)
            return MutationResult.success()
        except ValidationError as e:
            raise GraphQLError(str(e)) from e

    @strawberry.mutation(description="Force a workflow instance to a specific state (admin override).")
    def override_workflow_state(
        self,
        info: Info,
        instance_id: strawberry.ID,
        to_state: str,
        note: str = "",
    ) -> MutationResult:
        user = info.context.user
        if not user.is_superuser:
            raise GraphQLError("Only superusers can override workflow state")

        instance = WorkflowInstance.objects.filter(pk=instance_id).first()
        if not instance:
            raise GraphQLError(f"Workflow instance {instance_id} not found")

        from workflows.models import TransitionLog

        from_state = instance.current_state
        instance.current_state = to_state
        instance.updated_by = user
        instance.save()

        TransitionLog.objects.create(
            instance=instance,
            from_state=from_state,
            to_state=to_state,
            transitioned_by=user,
            note=f"[ADMIN OVERRIDE] {note}",
        )

        return MutationResult.success()

    @strawberry.mutation(description="Create a new workflow definition (platform operator only).")
    def create_workflow_definition(
        self,
        info: Info,
        name: str,
        slug: str,
        model_label: str,
        states: strawberry.scalars.JSON,
        transitions: strawberry.scalars.JSON,
        description: str | None = None,
        is_enabled: bool = False,
        pattern_kind: str | None = None,
        input_schema: strawberry.scalars.JSON | None = None,
    ) -> MutationResult:
        user = info.context.user
        _require_platform_operator(user)
        from core.run_input_contract import InputContractError, no_input_schema, validate_schema

        try:
            declared_schema = validate_schema(input_schema) if input_schema is not None else no_input_schema()
        except InputContractError as exc:
            return MutationResult(
                ok=False, errors=[GQLValidationError(field="input_schema", messages=[str(exc)])]
            )

        # pattern_kind drives the executor's composition (single / chained /
        # fan_out / ...). It existed on the model but had no creation arg, so
        # every API-created definition was stuck on the default "single" —
        # fan-out workflows were undefinable via the platform.
        valid_patterns = {c[0] for c in WorkflowDefinition.PatternKind.choices}
        if pattern_kind is not None and pattern_kind not in valid_patterns:
            return MutationResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="pattern_kind", messages=[f'Invalid pattern_kind "{pattern_kind}"']
                    )
                ],
            )

        workflow = WorkflowDefinition(
            name=name,
            slug=slug,
            model_label=model_label,
            states=states,
            transitions=transitions,
            description=description or "",
            is_enabled=is_enabled,
            pattern_kind=pattern_kind or WorkflowDefinition.PatternKind.SINGLE,
            input_schema=declared_schema,
            created_by=user,
            updated_by=user,
        )

        # Only validate if states are provided — empty workflows are valid
        # during creation (user will add states in the builder)
        if states:
            errors = workflow.validate_definition()
            if errors:
                return MutationResult(
                    ok=False,
                    errors=[GQLValidationError(field="definition", messages=errors)],
                )

        try:
            workflow.save()
        except Exception as e:
            raise GraphQLError(f"Failed to create workflow definition: {e}") from e

        return MutationResult.success()

    @strawberry.mutation(description="Update an org-owned workflow definition (globals are read-only).")
    @require_permission(Permission.WORKFLOW_UPDATE, scope=definition_scope_by_slug("slug"))
    @tenant_scoped()
    def update_workflow_definition(
        self,
        info: Info,
        slug: str,
        name: str | None = None,
        description: str | None = None,
        model_label: str | None = None,
        states: strawberry.scalars.JSON | None = None,
        transitions: strawberry.scalars.JSON | None = None,
        is_enabled: bool | None = None,
        pattern_kind: str | None = None,
        input_schema: strawberry.scalars.JSON | None = None,
    ) -> MutationResult:
        user = info.context.user

        workflow = _definition_for_write(slug)
        if not workflow:
            raise GraphQLError(f'Workflow definition "{slug}" not found')

        write_err = _definition_write_error(user, workflow)
        if write_err is not None:
            return MutationResult(
                ok=False,
                errors=[GQLValidationError(field=write_err[0], messages=[write_err[1]])],
            )

        if input_schema is not None:
            from core.run_input_contract import InputContractError, validate_schema

            try:
                workflow.input_schema = validate_schema(input_schema)
            except InputContractError as exc:
                return MutationResult(
                    ok=False, errors=[GQLValidationError(field="input_schema", messages=[str(exc)])]
                )

        if pattern_kind is not None:
            valid_patterns = {c[0] for c in WorkflowDefinition.PatternKind.choices}
            if pattern_kind not in valid_patterns:
                return MutationResult(
                    ok=False,
                    errors=[
                        GQLValidationError(
                            field="pattern_kind", messages=[f'Invalid pattern_kind "{pattern_kind}"']
                        )
                    ],
                )
            workflow.pattern_kind = pattern_kind

        if name is not None:
            workflow.name = name
        if description is not None:
            workflow.description = description
        if model_label is not None:
            workflow.model_label = model_label
        if states is not None:
            workflow.states = states
        if transitions is not None:
            workflow.transitions = transitions
        if is_enabled is False and workflow.is_enabled:
            from workflows.composition import workflow_parent_references

            parents = workflow_parent_references(workflow)
            if parents:
                parent_slugs = ", ".join(sorted({stage.definition.slug for stage in parents}))
                return MutationResult(
                    ok=False,
                    errors=[
                        GQLValidationError(
                            field="is_enabled",
                            messages=[
                                f'Cannot disable workflow definition "{slug}" — '
                                f"nested workflow stage(s) in {parent_slugs} still use it."
                            ],
                        )
                    ],
                )
        if is_enabled is not None:
            workflow.is_enabled = is_enabled

        workflow.updated_by = user

        # Agent/stage definitions carry no state machine (states == []) —
        # validate only when states exist, mirroring the create path.
        errors = workflow.validate_definition() if workflow.states else []
        if errors:
            return MutationResult(
                ok=False,
                errors=[GQLValidationError(field="definition", messages=errors)],
            )

        try:
            workflow.save()
        except Exception as e:
            raise GraphQLError(f"Failed to update workflow definition: {e}") from e

        return MutationResult.success()

    @strawberry.mutation(description="Soft-delete an org-owned workflow definition by slug.")
    @require_permission(Permission.WORKFLOW_DELETE, scope=definition_scope_by_slug("slug"))
    @tenant_scoped()
    def delete_workflow_definition(
        self,
        info: Info,
        slug: str,
    ) -> MutationResult:
        from django.utils import timezone

        user = info.context.user

        workflow = _definition_for_write(slug)
        if not workflow:
            raise GraphQLError(f'Workflow definition "{slug}" not found')

        write_err = _definition_write_error(user, workflow)
        if write_err is not None:
            return MutationResult(
                ok=False,
                errors=[GQLValidationError(field=write_err[0], messages=[write_err[1]])],
            )

        # PROTECT semantics for the tier-2 FK: a live configured Workflow
        # still runs off this shape — surface a clean error, not a 500.
        live_workflows = workflow.workflows.filter(deleted_at__isnull=True).count()
        if live_workflows:
            return MutationResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="slug",
                        messages=[
                            f'Cannot delete workflow definition "{slug}" — '
                            f"{live_workflows} configured Workflow(s) still use it. "
                            f"Delete those first."
                        ],
                    )
                ],
            )

        # Same PROTECT semantics for webhook triggers (createWorkflowTrigger →
        # astrolift_agents.WorkflowWebhook, consumed by the inbound webhook
        # view). The model is not soft-deletable — rows are append-only and
        # retired via ``enabled`` — so "live" means enabled here.
        live_triggers = workflow.webhooks.filter(enabled=True).count()
        if live_triggers:
            return MutationResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="slug",
                        messages=[
                            f'Cannot delete workflow definition "{slug}" — '
                            f"{live_triggers} enabled webhook trigger(s) still reference it. "
                            f"Disable those first."
                        ],
                    )
                ],
            )

        # Nested workflow references are slug-based rather than FKs. Enforce
        # the same PROTECT behavior explicitly so deleting a child cannot
        # leave an otherwise valid parent that only fails at dispatch time.
        from workflows.composition import workflow_parent_references

        parent_refs = workflow_parent_references(workflow)
        if parent_refs:
            parent_slugs = ", ".join(sorted({stage.definition.slug for stage in parent_refs}))
            return MutationResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="slug",
                        messages=[
                            f'Cannot delete workflow definition "{slug}" — '
                            f"nested workflow stage(s) in {parent_slugs} still use it."
                        ],
                    )
                ],
            )

        workflow.deleted_at = timezone.now()
        workflow.deleted_by = user
        workflow.save(update_fields=["deleted_at", "deleted_by", "updated_at", "version"])
        return MutationResult.success()

    @strawberry.mutation(
        description=(
            "Add a stage to a writable workflow definition. order=null appends "
            "after the definition's last stage."
        )
    )
    @require_permission(Permission.WORKFLOW_UPDATE, scope=definition_scope_by_slug("workflow_slug"))
    @tenant_scoped()
    def create_workflow_stage(
        self,
        info: Info,
        workflow_slug: str,
        kind: str,
        order: int | None = None,
        role: str | None = None,
        on_failure: str = "fail",
        timeout_seconds: int = 300,
        max_attempts: int = 3,
        agent_definition_guid: str | None = None,
        agent_ref: str | None = None,
        workflow_ref: str | None = None,
        environment_spec_slug: str | None = None,
        skill_refs: strawberry.scalars.JSON | None = None,
        fan_out_count: int | None = None,
        prompt: str | None = None,
        output_key: str | None = None,
        approvers: strawberry.scalars.JSON | None = None,
    ) -> CreateWorkflowStageResult:
        user = info.context.user

        workflow = _definition_for_write(workflow_slug)
        if not workflow:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="workflow_slug", messages=[f'Workflow "{workflow_slug}" not found']
                    )
                ],
            )

        write_err = _definition_write_error(user, workflow)
        if write_err is not None:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[GQLValidationError(field=write_err[0], messages=[write_err[1]])],
            )

        # Validate kind
        valid_kinds = {c[0] for c in WorkflowStage.StageKind.choices}
        if kind not in valid_kinds:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[GQLValidationError(field="kind", messages=[f'Invalid stage kind "{kind}"'])],
            )

        # Validate on_failure
        valid_failures = {c[0] for c in WorkflowStage.OnFailure.choices}
        if on_failure not in valid_failures:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[
                    GQLValidationError(field="on_failure", messages=[f'Invalid on_failure "{on_failure}"'])
                ],
            )

        from workflows.stage_limits import validate_stage_attempts

        try:
            validate_stage_attempts(max_attempts)
        except ValueError as exc:
            return CreateWorkflowStageResult(
                ok=False, errors=[GQLValidationError(field="max_attempts", messages=[str(exc)])]
            )

        # Resolve optional agent definition — scoped to the caller's org
        # (Workload's org lives via registered_app.organization); a foreign
        # org's workload resolves to not-found, never a cross-tenant binding.
        agent_definition = None
        if agent_definition_guid:
            from astrolift_registry.models import Workload

            agent_definition = Workload.objects.filter(
                guid=agent_definition_guid,
                registered_app__organization_id=_caller_org_pk(),
            ).first()
            if agent_definition is None:
                return CreateWorkflowStageResult(
                    ok=False,
                    errors=[
                        GQLValidationError(field="agent_definition_guid", messages=["Workload not found"])
                    ],
                )

        resolved_agent_ref = (agent_ref or "").strip()
        if agent_definition is not None:
            resolved_agent_ref = agent_definition.slug

        if order is None:
            # Append after the highest order ever used — soft-deleted stages
            # still occupy the (definition, order) unique constraint.
            from django.db.models import Max

            max_order = workflow.stages.aggregate(Max("order"))["order__max"]
            order = 0 if max_order is None else max_order + 1

        stage = WorkflowStage(
            definition=workflow,
            # Stage slug is unique (BaseCoreModel) but the mutation never set
            # it, so every stage defaulted to "none" and the 2nd stage created
            # anywhere collided — making multi-stage workflows impossible via
            # the API. Derive a unique, readable slug from (workflow, order).
            slug=f"{workflow.slug}-stage-{order}",
            order=order,
            kind=kind,
            role=role or "",
            on_failure=on_failure,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            agent_definition=agent_definition,
            agent_ref=resolved_agent_ref,
            workflow_ref=(workflow_ref or "").strip(),
            environment_spec_slug=(environment_spec_slug or "").strip(),
            skill_refs=skill_refs or [],
            fan_out_count=fan_out_count,
            prompt=prompt or "",
            output_key=(output_key or "").strip(),
            approvers=approvers or [],
            created_by=user,
            updated_by=user,
        )

        try:
            with transaction.atomic():
                stage.save()
                from workflows.composition import validate_workflow_composition

                validate_workflow_composition(workflow)
        except ValueError as exc:
            return CreateWorkflowStageResult(
                ok=False,
                errors=[GQLValidationError(field="workflow_ref", messages=[str(exc)])],
            )
        except Exception as e:
            raise GraphQLError(f"Failed to create workflow stage: {e}") from e

        return CreateWorkflowStageResult(ok=True, stage=stage)

    @strawberry.mutation(
        description="Create an inbound webhook trigger for a workflow definition (platform operator only)."
    )
    def create_workflow_trigger(self, info: Info, workflow_slug: str) -> CreateWorkflowTriggerResult:
        """Register a ``WorkflowWebhook`` that fires *workflow_slug* on inbound
        POST (#1019). The webhook is owned by the CALLER's active org — a
        WorkflowDefinition carries no org FK, and the org-level endpoint
        (``/api/webhooks/workflow/<org>/<slug>``) resolves + verifies by org —
        so the creator's org is the natural, secure owner. Returns the endpoint
        + plaintext signing secret (shown once). Fire with
        ``POST <endpoint>`` + header ``X-Astrolift-Signature: <secret>``.
        """
        from astrolift_agents.services.workflow_triggers import (
            create_webhook_workflow_trigger,
        )
        from astrolift_identity.models import Organization
        from core.tenancy import get_current_tenant

        user = info.context.user
        _require_platform_operator(user)

        workflow = WorkflowDefinition.objects.filter(slug=workflow_slug, deleted_at__isnull=True).first()
        if not workflow:
            return CreateWorkflowTriggerResult(
                ok=False,
                errors=[
                    GQLValidationError(
                        field="workflow_slug", messages=[f'Workflow "{workflow_slug}" not found']
                    )
                ],
            )

        # The webhook is owned by the caller's org. WorkflowWebhook.organization
        # is an astrolift_identity.Organization (NOT the profile's
        # organization.Organization — distinct models), so resolve it from the
        # tenant context the same way the agent/managed-service mutations do.
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        org = Organization.objects.filter(pk=org_pk, deleted_at__isnull=True).first() if org_pk else None
        if org is None:
            return CreateWorkflowTriggerResult(
                ok=False,
                errors=[
                    GQLValidationError(field="organization", messages=["no active organization in context"])
                ],
            )

        result = create_webhook_workflow_trigger(workflow, organization=org)
        return CreateWorkflowTriggerResult(
            ok=True,
            slug=result["slug"],
            endpoint=result["endpoint"],
            signing_secret=result["signing_secret"],
        )

    @strawberry.mutation(
        description=(
            "Deep-copy a visible workflow definition + its stages into the "
            "caller's org as a new editable definition (spec 40 §2.1). New slug "
            "on collision; global stages copy with agent_definition cleared."
        )
    )
    @require_permission(Permission.WORKFLOW_CREATE, scope=definition_scope_by_slug("slug"))
    @tenant_scoped()
    def clone_workflow_definition(
        self,
        info: Info,
        slug: str,
        org_id: strawberry.ID | None = None,
    ) -> CloneWorkflowDefinitionResult:
        user = info.context.user
        org, err = _resolve_clone_org(org_id)
        if err is not None:
            return err

        # Visible scope = caller's org UNION all platform-global definitions;
        # the org's own wins over a same-slug global (spec 40 §2.1).
        visible = WorkflowDefinition.objects.filter(slug=slug, deleted_at__isnull=True).filter(
            Q(organization_id=org.pk) | Q(organization__isnull=True)
        )
        source = visible.filter(organization_id=org.pk).first() or visible.first()
        if source is None:
            return CloneWorkflowDefinitionResult(
                ok=False,
                errors=[
                    GQLValidationError(field="slug", messages=[f'Workflow definition "{slug}" not visible'])
                ],
            )

        new_slug = _unique_clone_slug(source.slug, org)
        same_org = source.organization_id == org.pk
        with transaction.atomic():
            clone = WorkflowDefinition.objects.create(
                organization=org,
                # A project-owned parent may reference project-owned child
                # definitions. Preserve that packet boundary when cloning
                # within the same org; cross-org/global clones cannot carry
                # a foreign project FK.
                project=(source.project if same_org else None),
                name=source.name,
                slug=new_slug,
                description=source.description or "",
                model_label=source.model_label,
                pattern_kind=source.pattern_kind,
                states=source.states,
                transitions=source.transitions,
                input_schema=source.input_schema,
                is_enabled=source.is_enabled,
                created_by=user,
                updated_by=user,
            )
            for stage in source.stages.filter(deleted_at__isnull=True).order_by("order"):
                WorkflowStage.objects.create(
                    definition=clone,
                    slug=f"{new_slug}-stage-{stage.order}",
                    order=stage.order,
                    kind=stage.kind,
                    role=stage.role,
                    # Globals carry no org agent — clear unless cloning within
                    # the same org (spec 40 §2.1/§2.4).
                    agent_definition=(stage.agent_definition if same_org else None),
                    agent_ref=stage.agent_ref,
                    workflow_ref=stage.workflow_ref,
                    environment_spec_slug=stage.environment_spec_slug,
                    skill_refs=list(stage.skill_refs or []),
                    fan_out_count=stage.fan_out_count,
                    fan_out_dynamic=stage.fan_out_dynamic,
                    on_failure=stage.on_failure,
                    timeout_seconds=stage.timeout_seconds,
                    max_attempts=stage.max_attempts,
                    prompt=stage.prompt,
                    output_key=stage.output_key,
                    approvers=list(stage.approvers or []),
                    created_by=user,
                    updated_by=user,
                )
        return CloneWorkflowDefinitionResult(ok=True, slug=new_slug, definition=clone)
