"""ManagedServiceMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from strawberry.types import Info

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.scopes import agent_env_spec_scope
from astrolift_clusters import agent_test_jobs
from astrolift_clusters.models import TenantCluster
from astrolift_drivers.isolation import IsolationError, parse_mode
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Project
from astrolift_identity.operation_context import (
    agent_region_operation,
    environment_operation,
    managed_service_operation,
    named_environment,
    project_service_creation_operation,
)
from astrolift_identity.step_up import requires_elevation
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_services.model_prompt import PromptReadinessState, live_model_prompt_service, prompt_readiness
from astrolift_services.models import (
    ManagedService,
    ManagedServiceAttachment,
)
from astrolift_services.schema.mutations.helpers import (
    _caller_org_id,
    _client_ip,
    _is_envelope_key_public,
)
from astrolift_services.schema.mutations.types import (
    AdoptManagedResourceInput,
    AttachProjectManagedServiceInput,
    DeprovisionManagedServiceInput,
    DetachProjectManagedServiceInput,
    ProvisionManagedServiceInput,
    ProvisionProjectManagedServiceInput,
    ReprovisionManagedServiceInput,
    RevealManagedServiceConnectionInput,
    TestModelEndpointInput,
    UpdateManagedServiceInput,
    _ManagedResourceAdoptionPayload,
    _ManagedServiceDeletedPayload,
)
from astrolift_services.schema.types import (
    ManagedServiceAttachmentType,
    ManagedServiceConnectionKeyType,
    ManagedServiceConnectionType,
    ManagedServiceType,
    ModelEndpointTestType,
    managed_service_attachment_to_type,
    managed_service_to_type,
)
from astrolift_services.scopes import (
    _app_scope_via,
    assert_provider_cluster,
    managed_service_attachment_scope,
    managed_service_scope_by_guid,
)
from astrolift_services.scopes import services_app_scope_by_slug as app_scope_by_slug
from astrolift_services.scopes import services_project_scope_by_guid as project_scope_by_guid
from core.decorators import tenant_scoped
from core.mutations import AuditEntry, ErrorCode, emit_audit, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@require_permission(
    Permission.PROJECT_UPDATE,
    scope=_app_scope_via(
        "astrolift_lifecycle.AppEnvironment", "app_environment_id", permissions=(Permission.PROJECT_UPDATE,)
    ),
    operation=environment_operation("app_environment_id"),
)
def _authorize_app_consumer(app_environment_id):
    return None


@require_permission(
    Permission.PROJECT_UPDATE,
    scope=agent_env_spec_scope("spec_slug", Permission.PROJECT_UPDATE),
    operation=agent_region_operation,
)
def _authorize_agent_consumer(spec_slug):
    return None


def _creation_operation(args):
    from core.scope_args import read_arg

    normalized = {
        "project_id": read_arg(args, "input.project_id"),
        "cluster_id": read_arg(args, "input.cluster_id"),
        "environment_name": str(read_arg(args, "input.environment_name") or "production").strip(),
    }
    return project_service_creation_operation({"input": normalized})


def _attachment_operation(args):
    from core.scope_args import read_guid

    service = (
        ManagedServiceAttachment.objects.filter(
            guid=read_guid(args, "input.attachment_id"),
            managed_service__project__organization_id=_caller_org_id(),
        )
        .values_list("managed_service__guid", flat=True)
        .first()
    )
    return managed_service_operation("service_id")({"service_id": str(service)})


# Columns an accepted in-place update writes. Both update mutations save with
# this scope rather than a bare ``save()``: the row is only locked against
# other ``select_for_update`` writers, so a full-row write would push the
# snapshot read at the top of the transaction back over whatever the update
# workflow's activities (``backend_ref``, ``connection_secret_ref``) or
# ``revealManagedServiceConnection`` (``last_action_at``) wrote meanwhile.
_UPDATE_DESIRED_STATE_FIELDS = (
    "applied_config",
    "config",
    "status",
    "status_error",
    "operation_kind",
    "operation_workflow_id",
    "operation_run_id",
    "operation_started_at",
    "operation_completed_at",
)


def _creator_id() -> int | None:
    """The caller's user pk, for a service's ``created_by`` (its deployedBy, #2155)."""
    tenant = get_current_tenant()
    return tenant.actor_user_id if tenant else None


def _requested_isolation(value: str | None) -> str:
    """The isolation mode to store on the row, or ``""`` for unspecified.

    Refused here rather than at provision time: the resolver is reached from
    a Temporal activity, where an unparseable mode surfaces as a retrying
    provision failure instead of a message to whoever typed it.
    """
    mode = parse_mode(value)
    return mode.value if mode is not None else ""


def _refuse_unscoped_config_secret_refs(config, *, owner, cluster, code: str = ErrorCode.VALIDATION.value):
    """A failure envelope naming the first secret ref in ``config`` outside
    the namespace of ``owner``, the app or project owning the service (#1921),
    else ``None``. ``cluster`` is the service's cluster."""
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError
    from astrolift_services.secret_ref_config import assert_config_secret_refs_scoped

    try:
        assert_config_secret_refs_scoped(dict(config or {}), owner=owner, cluster=cluster)
    except SecretRefNamespaceError as exc:
        return gql_failure(code, str(exc), field="config")
    return None


def _project_service_rows_for_caller(service_id):
    rows = (
        ManagedService.objects.select_related("project", "tenant_cluster")
        .prefetch_related(
            "attachments__agent_environment_spec",
            "attachments__app_environment__registered_app",
        )
        .filter(
            guid=str(service_id),
            project__organization_id=_caller_org_id(),
            deleted_at__isnull=True,
        )
    )
    tenant = get_current_tenant()
    if tenant is not None and tenant.team_id is not None:
        rows = rows.filter(project__team_id=tenant.team_id)
    return rows


def _project_service_for_caller(service_id):
    return _project_service_rows_for_caller(service_id).first()


def _managed_service_for_caller(service_id):
    """One managed service in the caller's org, whichever scope owns it.

    Explicitly org-filtered on *both* ownership branches. ``@tenant_scoped()``
    asserts a tenant context and filters nothing, and ``_caller_org_id()``
    returns ``None`` with no tenant, which an unguarded ``filter()`` would read
    as "every organization". The ``org is None`` guard makes that deny rather
    than match, which is the difference between a not-found and a cross-tenant
    takeover of somebody else's cloud resource (#1042 / #1183).
    """
    org_id = _caller_org_id()
    if org_id is None:
        return None
    rows = ManagedService.objects.select_related(
        "registered_app__organization",
        "project__organization",
        "app_environment__tenant_cluster__provider_plugin",
        "tenant_cluster__provider_plugin",
    ).filter(
        Q(registered_app__organization_id=org_id) | Q(project__organization_id=org_id),
        guid=str(service_id),
        deleted_at__isnull=True,
    )
    # Narrow a project-owned row to the tenant's team the same way
    # ``_project_service_rows_for_caller`` does. App-owned rows keep the
    # organization scope the app mutations use, so this tightens the project
    # branch rather than changing what an app-scoped caller can already reach.
    tenant = get_current_tenant()
    if tenant is not None and tenant.team_id is not None:
        rows = rows.filter(Q(project__isnull=True) | Q(project__team_id=tenant.team_id))
    return rows.first()


def _workflow_actor(info: Info):
    from astrolift_workflows.inputs import Actor

    request = info.context.request  # type: ignore[attr-defined]
    token = getattr(request, "_api_token", None)
    if token is not None:
        return Actor(
            kind="api_token",
            user_id=token.user_id,
            token_id=token.pk,
            display=token.name,
        )
    user = getattr(request, "user", None)
    return Actor(
        kind="user",
        user_id=getattr(user, "pk", None) if user is not None else None,
        display=str(getattr(user, "email", "") or getattr(user, "username", "")),
    )


def _start_project_service_provision(info: Info, svc: ManagedService) -> None:
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import ProvisionManagedServiceInput as ProvisionInput

    start_workflow(
        "ProvisionManagedServiceWorkflow",
        args=[ProvisionInput(managed_service_id=svc.pk, actor=_workflow_actor(info))],
        workflow_id=f"ProvisionManagedServiceWorkflow-{svc.guid}",
    )


def _start_project_service_deprovision(
    info: Info,
    svc: ManagedService,
    *,
    delete_data: bool,
    force_destroy: bool,
) -> None:
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import DeprovisionManagedServiceInput as DeprovisionInput

    start_workflow(
        "DeprovisionManagedServiceWorkflow",
        args=[
            DeprovisionInput(
                managed_service_id=svc.pk,
                actor=_workflow_actor(info),
                delete_data=delete_data,
                force_destroy=force_destroy,
            )
        ],
        workflow_id=f"DeprovisionManagedServiceWorkflow-{svc.guid}",
    )


def _start_service_update(info: Info, svc: ManagedService) -> None:
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import UpdateManagedServiceInput as UpdateInput

    workflow_id = f"UpdateManagedServiceWorkflow-{svc.guid}"
    handle = start_workflow(
        "UpdateManagedServiceWorkflow",
        args=[UpdateInput(managed_service_id=svc.pk, actor=_workflow_actor(info))],
        workflow_id=workflow_id,
    )
    if not handle.enqueued:
        raise RuntimeError("Temporal workflow runtime is disabled; update was not enqueued")
    svc.operation_run_id = str(handle.run_id or "")
    svc.save(update_fields=["operation_run_id", "updated_at", "version"])


def _mark_update_enqueue_failed(svc: ManagedService, exc: Exception) -> None:
    svc.status = ManagedService.Status.FAILED
    svc.status_error = f"could not enqueue managed-service update: {exc}"[:4000]
    svc.operation_completed_at = timezone.now()
    svc.save(
        update_fields=[
            "status",
            "status_error",
            "operation_completed_at",
            "updated_at",
            "version",
        ],
    )


@strawberry.type
class ManagedServiceMutations:
    # ---- Managed services CRUD (#281) ----------------------------

    @strawberry.field
    @mutation_audit(action="project.managed_service.provision")
    @require_permission(
        Permission.PROJECT_UPDATE,
        scope=project_scope_by_guid("input.project_id", permissions=(Permission.PROJECT_UPDATE,)),
        operation=_creation_operation,
    )
    @tenant_scoped()
    def provision_project_managed_service(
        self,
        info: Info,
        input: ProvisionProjectManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        org_id = _caller_org_id()
        project_rows = Project.objects.filter(
            guid=str(input.project_id),
            organization_id=org_id,
            deleted_at__isnull=True,
        )
        tenant = get_current_tenant()
        if tenant is not None and tenant.team_id is not None:
            project_rows = project_rows.filter(team_id=tenant.team_id)
        project = project_rows.first()
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found", field="projectId")
        cluster = TenantCluster.objects.filter(
            Q(organization_id=org_id) | Q(organization_id__isnull=True),
            guid=str(input.cluster_id),
            deleted_at__isnull=True,
            is_active=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        ).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        from astrolift_services.managed_service_catalog import (
            CatalogResolutionError,
            resolve_variant,
            validate_config,
        )

        try:
            catalog_item = resolve_variant(
                plugin_slug=cluster.provider_plugin.slug,
                kind=input.kind,
                requested_variant=input.variant,
            )
            if catalog_item is not None:
                validate_config(catalog_item, dict(input.config or {}))
        except CatalogResolutionError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field=exc.field)
        refused = _refuse_unscoped_config_secret_refs(input.config, owner=project, cluster=cluster)
        if refused is not None:
            return refused
        if catalog_item is None:
            valid_kinds = {kind for kind, _label in ManagedService.Kind.choices}
            if input.kind not in valid_kinds:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"kind must be one of {sorted(valid_kinds)}",
                    field="kind",
                )
        resolved_variant = catalog_item.variant if catalog_item is not None else (input.variant or "")
        try:
            isolation = _requested_isolation(input.isolation)
        except IsolationError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field="isolation")
        name = (input.name or input.kind).strip()
        if ManagedService.objects.filter(
            project=project,
            kind=input.kind,
            name=name,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"managed service ({input.kind}, {name!r}) already exists for this project",
                field="name",
            )

        agent_specs = list(
            AgentEnvironmentSpec.objects.filter(
                organization_id=org_id,
                slug__in=list(input.agent_environment_spec_slugs),
                deleted_at__isnull=True,
            )
        )
        allowed_agent_spec_slugs = set(
            Workload.objects.filter(
                registered_app__project=project,
                kind=Workload.Kind.AGENT,
                deleted_at__isnull=True,
            ).values_list("slug", flat=True)
        )
        from workflows.models import WorkflowStage

        allowed_agent_spec_slugs.update(
            WorkflowStage.objects.filter(
                definition__project=project,
                deleted_at__isnull=True,
            ).values_list("environment_spec_slug", flat=True)
        )
        if any(spec.slug not in allowed_agent_spec_slugs for spec in agent_specs) or len(agent_specs) != len(
            set(input.agent_environment_spec_slugs)
        ):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "every agent environment spec must belong to this project",
                field="agentEnvironmentSpecSlugs",
            )
        if agent_specs:
            from astrolift_agents.services.agent_cluster import (
                NoAgentClusterError,
                resolve_agent_cluster,
            )

            try:
                agent_cluster = resolve_agent_cluster(project.organization)
            except NoAgentClusterError:
                agent_cluster = None
            if agent_cluster is None or agent_cluster.pk != cluster.pk:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "agent consumers must run on the managed service cluster",
                    field="agentEnvironmentSpecSlugs",
                )

        app_envs = list(
            AppEnvironment.objects.select_related("registered_app").filter(
                guid__in=[str(value) for value in input.app_environment_ids],
                registered_app__project=project,
                registered_app__organization_id=org_id,
                tenant_cluster=cluster,
                deleted_at__isnull=True,
            )
        )
        if len(app_envs) != len(set(input.app_environment_ids)):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "every app environment must belong to this project and use the managed service cluster",
                field="appEnvironmentIds",
            )

        for spec in agent_specs:
            _authorize_agent_consumer(spec.slug)
        for env in app_envs:
            _authorize_app_consumer(str(env.guid))

        svc = ManagedService.objects.create(
            project=project,
            tenant_cluster=cluster,
            environment_name=(input.environment_name or "production").strip(),
            kind=input.kind,
            name=name,
            variant=resolved_variant,
            isolation=isolation,
            config=dict(input.config or {}),
            status=ManagedService.Status.PENDING,
            created_by_id=_creator_id(),
        )
        for spec in agent_specs:
            ManagedServiceAttachment.objects.create(
                managed_service=svc,
                agent_environment_spec=spec,
            )
        for env in app_envs:
            ManagedServiceAttachment.objects.create(
                managed_service=svc,
                app_environment=env,
            )
        _start_project_service_provision(info, svc)
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="project.managed_service.attach")
    @require_permission(
        Permission.PROJECT_UPDATE,
        scope=managed_service_scope_by_guid(
            "input.managed_service_id", permissions=(Permission.PROJECT_UPDATE,)
        ),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def attach_project_managed_service(
        self,
        info: Info,
        input: AttachProjectManagedServiceInput,
    ) -> MutationResultType[ManagedServiceAttachmentType]:
        svc = _project_service_for_caller(input.managed_service_id)
        if svc is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project managed service not found")
        if bool(input.agent_environment_spec_slug) == bool(input.app_environment_id):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "provide exactly one agentEnvironmentSpecSlug or appEnvironmentId",
            )
        if input.agent_environment_spec_slug:
            spec = AgentEnvironmentSpec.objects.filter(
                organization_id=svc.project.organization_id,
                slug=input.agent_environment_spec_slug,
                deleted_at__isnull=True,
            ).first()
            allowed = Workload.objects.filter(
                registered_app__project=svc.project,
                registered_app__project__organization_id=svc.project.organization_id,
                kind=Workload.Kind.AGENT,
                slug=input.agent_environment_spec_slug,
                deleted_at__isnull=True,
            ).exists()
            if not allowed:
                from workflows.models import WorkflowStage

                allowed = WorkflowStage.objects.filter(
                    definition__project=svc.project,
                    definition__project__organization_id=svc.project.organization_id,
                    environment_spec_slug=input.agent_environment_spec_slug,
                    deleted_at__isnull=True,
                ).exists()
            if spec is None or not allowed:
                return gql_failure(ErrorCode.NOT_FOUND.value, "agent environment spec not found")
            from astrolift_agents.services.agent_cluster import (
                NoAgentClusterError,
                resolve_agent_cluster,
            )

            try:
                agent_cluster = resolve_agent_cluster(spec.organization)
            except NoAgentClusterError:
                agent_cluster = None
            if agent_cluster is None or agent_cluster.pk != svc.tenant_cluster_id:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "agent runtime cluster does not match the managed service cluster",
                )
            _authorize_agent_consumer(spec.slug)
            row, _created = ManagedServiceAttachment.objects.get_or_create(
                managed_service=svc,
                agent_environment_spec=spec,
            )
        else:
            env = AppEnvironment.objects.filter(
                guid=str(input.app_environment_id),
                registered_app__project=svc.project,
                registered_app__project__organization_id=svc.project.organization_id,
                tenant_cluster=svc.tenant_cluster,
                deleted_at__isnull=True,
            ).first()
            if env is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "app environment not found")
            _authorize_app_consumer(str(env.guid))
            row, _created = ManagedServiceAttachment.objects.get_or_create(
                managed_service=svc,
                app_environment=env,
            )
        return gql_success(managed_service_attachment_to_type(row))

    @strawberry.field
    @mutation_audit(action="project.managed_service.detach")
    @require_permission(
        Permission.PROJECT_UPDATE,
        scope=managed_service_attachment_scope(permissions=(Permission.PROJECT_UPDATE,)),
        operation=_attachment_operation,
    )
    @tenant_scoped()
    def detach_project_managed_service(
        self,
        info: Info,
        input: DetachProjectManagedServiceInput,
    ) -> MutationResultType[ManagedServiceAttachmentType]:
        rows = ManagedServiceAttachment.objects.select_related(
            "managed_service__project",
            "agent_environment_spec",
            "app_environment__registered_app",
        ).filter(
            guid=str(input.attachment_id),
            managed_service__project__organization_id=_caller_org_id(),
            deleted_at__isnull=True,
        )
        tenant = get_current_tenant()
        if tenant is not None and tenant.team_id is not None:
            rows = rows.filter(managed_service__project__team_id=tenant.team_id)
        row = rows.first()
        if row is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "attachment not found")
        if row.app_environment_id:
            _authorize_app_consumer(str(row.app_environment.guid))
        else:
            _authorize_agent_consumer(row.agent_environment_spec.slug)
        payload = managed_service_attachment_to_type(row)
        row.soft_delete()
        return gql_success(payload)

    @strawberry.field
    @mutation_audit(action="project.managed_service.update")
    @require_permission(
        Permission.PROJECT_UPDATE,
        scope=managed_service_scope_by_guid("input.id", permissions=(Permission.PROJECT_UPDATE,)),
        operation=managed_service_operation("input.id"),
    )
    @tenant_scoped()
    def update_project_managed_service(
        self,
        info: Info,
        input: UpdateManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        config_changed = False
        changed_fields: list[str] = []
        with transaction.atomic():
            # The scoped queryset joins nullable app/agent relationships for
            # authorization and rendering. Lock only the service row: Postgres
            # rejects FOR UPDATE against the nullable side of an outer join.
            svc = _project_service_rows_for_caller(input.id).select_for_update(of=("self",)).first()
            if svc is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "project managed service not found")
            incoming = dict(input.config) if input.config is not None else None
            if incoming is not None and incoming != (svc.config or {}):
                from astrolift_services.schema.types import _editable_fields_for

                refused = _refuse_unscoped_config_secret_refs(
                    incoming, owner=svc.project, cluster=svc.effective_cluster
                )
                if refused is not None:
                    return refused
                editable = _editable_fields_for(svc)
                if editable != ["*"]:
                    changed = {
                        key
                        for key in set(incoming) | set(svc.config or {})
                        if incoming.get(key) != (svc.config or {}).get(key)
                    }
                    blocked = changed - set(editable)
                    if blocked:
                        return gql_failure(
                            ErrorCode.VALIDATION.value,
                            f"fields {sorted(blocked)} require reprovision",
                            field="config",
                        )
                if svc.status != ManagedService.Status.ACTIVE:
                    return gql_failure(
                        ErrorCode.PRECONDITION.value,
                        f"managed service is {svc.status}; wait for it to become active",
                        field="id",
                    )
                if not svc.backend_ref:
                    return gql_failure(
                        ErrorCode.PRECONDITION.value,
                        "managed service has no backend resource; reprovision it instead",
                        field="id",
                    )
                if svc.applied_config is None:
                    svc.applied_config = dict(svc.config or {})
                svc.config = incoming
                svc.status = ManagedService.Status.UPDATING
                svc.status_error = ""
                svc.operation_kind = "update"
                svc.operation_workflow_id = f"UpdateManagedServiceWorkflow-{svc.guid}"
                svc.operation_run_id = ""
                svc.operation_started_at = timezone.now()
                svc.operation_completed_at = None
                config_changed = True
                changed_fields += _UPDATE_DESIRED_STATE_FIELDS
            if input.name is not None and input.name.strip() != svc.name:
                svc.name = input.name.strip()
                changed_fields.append("name")
            if changed_fields:
                svc.save(update_fields=[*changed_fields, "updated_at", "version"])
        if config_changed:
            try:
                _start_service_update(info, svc)
            except Exception as exc:  # noqa: BLE001
                _mark_update_enqueue_failed(svc, exc)
                return gql_failure(
                    ErrorCode.INTERNAL.value,
                    "managed-service update could not be enqueued",
                )
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="project.managed_service.reprovision")
    @require_permission(
        Permission.PROJECT_UPDATE,
        scope=managed_service_scope_by_guid(
            "input.managed_service_id", permissions=(Permission.PROJECT_UPDATE,)
        ),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def reprovision_project_managed_service(
        self,
        info: Info,
        input: ReprovisionManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        svc = _project_service_for_caller(input.managed_service_id)
        if svc is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project managed service not found")
        if svc.status in {
            ManagedService.Status.DEPROVISIONING,
            ManagedService.Status.PROVISIONING,
        }:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"managed service is {svc.status}",
            )
        svc.status = ManagedService.Status.PENDING
        svc.save(update_fields=["status", "updated_at", "version"])
        _start_project_service_provision(info, svc)
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="project.managed_service.deprovision")
    @requires_elevation(action_label="project.managed_service.deprovision")
    @require_permission(
        Permission.PROJECT_UPDATE,
        scope=managed_service_scope_by_guid("input.id", permissions=(Permission.PROJECT_UPDATE,)),
        operation=managed_service_operation("input.id"),
    )
    @tenant_scoped()
    def deprovision_project_managed_service(
        self,
        info: Info,
        input: DeprovisionManagedServiceInput,
    ) -> MutationResultType[_ManagedServiceDeletedPayload]:
        svc = _project_service_for_caller(input.id)
        if svc is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project managed service not found")
        svc.status = ManagedService.Status.DEPROVISIONING
        svc.save(update_fields=["status", "updated_at", "version"])
        _start_project_service_deprovision(
            info,
            svc,
            delete_data=bool(input.delete_data),
            force_destroy=bool(input.force_destroy),
        )
        return gql_success(_ManagedServiceDeletedPayload(id=input.id, deleted=False))

    @strawberry.field
    @mutation_audit(action="managed_service.provision")
    @require_permission(
        Permission.APP_UPDATE,
        scope=app_scope_by_slug("input.app_slug", permissions=(Permission.APP_UPDATE,)),
        operation=named_environment(),
    )
    @tenant_scoped()
    def provision_managed_service(
        self,
        info: Info,
        input: ProvisionManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        """Provision a managed-service binding.

        DB-side write only — the actual workflow that drives the
        provider plugin's provision() lives in
        ``astrolift_workflows`` and reads from this row. The
        mutation creates the row in PENDING state; the workflow
        loop transitions it through PROVISIONING → ACTIVE."""
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=_caller_org_id()).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        env = AppEnvironment.objects.filter(
            registered_app=app,
            name=input.environment_name,
            deleted_at__isnull=True,
        ).first()
        if env is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"environment {input.environment_name!r} not found",
                field="environmentName",
            )
        assert_provider_cluster(env.tenant_cluster, permission=Permission.APP_UPDATE)
        from astrolift_services.managed_service_catalog import (
            CatalogResolutionError,
            resolve_variant,
            validate_config,
        )

        try:
            catalog_item = resolve_variant(
                plugin_slug=env.tenant_cluster.provider_plugin.slug,
                kind=input.kind,
                requested_variant=input.variant,
            )
            if catalog_item is not None:
                validate_config(catalog_item, dict(input.config or {}))
        except CatalogResolutionError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field=exc.field)
        refused = _refuse_unscoped_config_secret_refs(input.config, owner=app, cluster=env.tenant_cluster)
        if refused is not None:
            return refused
        if catalog_item is None:
            valid_kinds = {k for k, _ in ManagedService.Kind.choices}
            if input.kind not in valid_kinds:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"kind must be one of {sorted(valid_kinds)}",
                    field="kind",
                )
        resolved_variant = catalog_item.variant if catalog_item is not None else (input.variant or "")
        try:
            isolation = _requested_isolation(input.isolation)
        except IsolationError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field="isolation")
        name = (input.name or input.kind).strip()
        if ManagedService.objects.filter(
            registered_app=app,
            app_environment=env,
            kind=input.kind,
            name=name,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"managed service ({input.kind}, {name!r}) already exists for this app environment",
                field="name",
            )
        svc = ManagedService.objects.create(
            registered_app=app,
            app_environment=env,
            kind=input.kind,
            name=name,
            variant=resolved_variant,
            isolation=isolation,
            config=dict(input.config or {}),
            status=ManagedService.Status.PENDING,
            created_by_id=_creator_id(),
        )

        # Fire the workflow that actually provisions the backend resource
        # and transitions the row PENDING -> PROVISIONING -> ACTIVE (#1001).
        # Before this, the row was created and nothing drove it — it sat
        # PENDING forever. Deterministic id de-dups re-fires via Temporal.
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import (
            Actor,
        )
        from astrolift_workflows.inputs import (
            ProvisionManagedServiceInput as ProvisionInput,
        )

        request = info.context.request  # type: ignore[attr-defined]
        user = getattr(request, "user", None)
        actor = Actor(
            kind="user",
            user_id=getattr(user, "pk", None) if user is not None else None,
            display=str(getattr(user, "email", "") or getattr(user, "username", "")),
        )
        start_workflow(
            "ProvisionManagedServiceWorkflow",
            args=[
                ProvisionInput(
                    managed_service_id=svc.pk,
                    actor=actor,
                ),
            ],
            workflow_id=f"ProvisionManagedServiceWorkflow-{svc.guid}",
        )
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="managed_service.update")
    @require_permission(
        Permission.APP_UPDATE,
        scope=managed_service_scope_by_guid("input.id", permissions=(Permission.APP_UPDATE,)),
        operation=managed_service_operation("input.id"),
    )
    @tenant_scoped()
    def update_managed_service(
        self,
        info: Info,
        input: UpdateManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        config_changed = False
        changed_fields: list[str] = []
        with transaction.atomic():
            svc = (
                # ``app_environment`` is nullable for project services. Lock
                # only the desired-state row, not nullable joined relations.
                ManagedService.objects.select_for_update(of=("self",))
                .select_related(
                    "app_environment__tenant_cluster__provider_plugin",
                    "registered_app",
                )
                .filter(
                    guid=str(input.id),
                    registered_app__organization_id=_caller_org_id(),
                    deleted_at__isnull=True,
                )
                .first()
            )
            if svc is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "managed service not found",
                )
            incoming = dict(input.config) if input.config is not None else None
            if incoming is not None and incoming != (svc.config or {}):
                from astrolift_services.schema.types import _editable_fields_for

                refused = _refuse_unscoped_config_secret_refs(
                    incoming, owner=svc.registered_app, cluster=svc.effective_cluster
                )
                if refused is not None:
                    return refused
                editable = _editable_fields_for(svc)
                if editable != ["*"]:
                    changed_keys = {
                        key
                        for key in set(incoming) | set(svc.config or {})
                        if incoming.get(key) != (svc.config or {}).get(key)
                    }
                    blocked = changed_keys - set(editable)
                    if blocked:
                        return gql_failure(
                            ErrorCode.VALIDATION.value,
                            f"fields {sorted(blocked)} cannot be changed in-place; "
                            "use reprovisionManagedService to apply them",
                            field="config",
                        )
                if svc.status != ManagedService.Status.ACTIVE:
                    return gql_failure(
                        ErrorCode.PRECONDITION.value,
                        f"managed service is {svc.status}; wait for it to become active",
                        field="id",
                    )
                if not svc.backend_ref:
                    return gql_failure(
                        ErrorCode.PRECONDITION.value,
                        "managed service has no backend resource; reprovision it instead",
                        field="id",
                    )
                if svc.applied_config is None:
                    svc.applied_config = dict(svc.config or {})
                svc.config = incoming
                svc.status = ManagedService.Status.UPDATING
                svc.status_error = ""
                svc.operation_kind = "update"
                svc.operation_workflow_id = f"UpdateManagedServiceWorkflow-{svc.guid}"
                svc.operation_run_id = ""
                svc.operation_started_at = timezone.now()
                svc.operation_completed_at = None
                config_changed = True
                changed_fields += _UPDATE_DESIRED_STATE_FIELDS
            if input.name is not None and input.name.strip() != svc.name:
                svc.name = input.name.strip()
                changed_fields.append("name")
            if changed_fields:
                svc.save(update_fields=[*changed_fields, "updated_at", "version"])
        if config_changed:
            try:
                _start_service_update(info, svc)
            except Exception as exc:  # noqa: BLE001
                _mark_update_enqueue_failed(svc, exc)
                return gql_failure(
                    ErrorCode.INTERNAL.value,
                    "managed-service update could not be enqueued",
                )
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="managed_service.reprovision")
    @require_permission(
        Permission.APP_UPDATE,
        scope=managed_service_scope_by_guid("input.managed_service_id", permissions=(Permission.APP_UPDATE,)),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def reprovision_managed_service(
        self,
        info: Info,
        input: ReprovisionManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        """Trigger a full reprovision cycle for a managed service (#745).

        Transitions status to PENDING so the lifecycle workflow picks it
        up for a fresh provision pass. Use for config changes that are
        NOT in ``editable_fields`` (i.e., changes that require tearing
        down and re-creating the backing cloud resource).
        """
        svc = (
            ManagedService.objects.select_related(
                "app_environment__tenant_cluster__provider_plugin",
                "registered_app",
            )
            .filter(
                guid=str(input.managed_service_id),
                registered_app__organization_id=_caller_org_id(),
                deleted_at__isnull=True,
            )
            .first()
        )
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
                field="managedServiceId",
            )
        _blocked = {
            ManagedService.Status.DEPROVISIONING,
            ManagedService.Status.PROVISIONING,
        }
        if svc.status in _blocked:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"managed service is {svc.status}; reprovision can only be "
                "triggered for services that are active, pending, updating, or failed",
                field="managedServiceId",
            )
        svc.status = ManagedService.Status.PENDING
        svc.save(update_fields=["status", "updated_at", "version"])

        # Re-fire ProvisionManagedServiceWorkflow so a row that is failed/
        # pending/active actually re-runs (#1038). Before this the mutation
        # only flipped status to PENDING and returned ok=true — but nothing
        # drove the row, so it sat PENDING forever (live finding: reprovision
        # on a failed row never re-ran). Deterministic id + TERMINATE_IF_RUNNING
        # single-flights re-fires: a still-running run is superseded, a dead
        # run is replaced with a fresh one. Mirrors provision_managed_service.
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import (
            Actor,
        )
        from astrolift_workflows.inputs import (
            ProvisionManagedServiceInput as ProvisionInput,
        )

        request = info.context.request  # type: ignore[attr-defined]
        user = getattr(request, "user", None)
        actor = Actor(
            kind="user",
            user_id=getattr(user, "pk", None) if user is not None else None,
            display=str(getattr(user, "email", "") or getattr(user, "username", "")),
        )
        start_workflow(
            "ProvisionManagedServiceWorkflow",
            args=[
                ProvisionInput(
                    managed_service_id=svc.pk,
                    actor=actor,
                ),
            ],
            workflow_id=f"ProvisionManagedServiceWorkflow-{svc.guid}",
        )
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="managed_service.deprovision")
    @require_permission(
        Permission.APP_UPDATE,
        scope=managed_service_scope_by_guid("input.id", permissions=(Permission.APP_UPDATE,)),
        operation=managed_service_operation("input.id"),
    )
    @tenant_scoped()
    def deprovision_managed_service(
        self,
        info: Info,
        input: DeprovisionManagedServiceInput,
    ) -> MutationResultType[_ManagedServiceDeletedPayload]:
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import (
            Actor,
        )
        from astrolift_workflows.inputs import (
            DeprovisionManagedServiceInput as DeprovisionInput,
        )

        svc = ManagedService.objects.filter(
            guid=str(input.id),
            registered_app__organization_id=_caller_org_id(),
            deleted_at__isnull=True,
        ).first()
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
            )

        # Flip to DEPROVISIONING so the UI shows the in-flight state
        # immediately. The workflow re-asserts on entry; the platform
        # row is only soft-deleted by the workflow's finalize activity
        # AFTER the driver confirms the backend resource is gone.
        svc.status = ManagedService.Status.DEPROVISIONING
        svc.save(
            update_fields=[
                "status",
                "updated_at",
                "version",
            ]
        )

        request = info.context.request  # type: ignore[attr-defined]
        user = getattr(request, "user", None)
        actor = Actor(
            kind="user",
            user_id=getattr(user, "pk", None) if user is not None else None,
            display=str(getattr(user, "email", "") or getattr(user, "username", "")),
        )
        start_workflow(
            "DeprovisionManagedServiceWorkflow",
            args=[
                DeprovisionInput(
                    managed_service_id=svc.pk,
                    actor=actor,
                    delete_data=bool(input.delete_data),
                    force_destroy=bool(input.force_destroy),
                ),
            ],
            workflow_id=f"DeprovisionManagedServiceWorkflow-{svc.guid}",
        )
        return gql_success(
            _ManagedServiceDeletedPayload(
                id=input.id,
                deleted=False,  # workflow finalizes the soft-delete
            )
        )

    @strawberry.field
    @mutation_audit(
        action="managed_service.resource.adopt",
        extras=lambda result: (
            {
                "resource_id": result.data.resource_id,
                "classification": result.data.classification,
                "prior_managed_service_id": result.data.prior_managed_service_id,
                "surface": result.data.surface,
            }
            if result.ok and result.data is not None
            else None
        ),
    )
    @requires_elevation(action_label="managed_service.resource.adopt")
    @require_permission(
        Permission.MANAGED_SERVICE_ADOPT,
        scope=managed_service_scope_by_guid("input.id", permissions=(Permission.MANAGED_SERVICE_ADOPT,)),
        operation=managed_service_operation("input.id"),
    )
    @tenant_scoped()
    def adopt_managed_resource(
        self,
        info: Info,
        input: AdoptManagedResourceInput,
    ) -> MutationResultType[_ManagedResourceAdoptionPayload]:
        """Bring an existing cloud resource under a managed service (#1365).

        The last acceptance criterion of #1365 and the migration path off the
        fail-closed change #1443 / #1446 shipped: a resource provisioned before
        its driver stamped an identity tag is refused on every mutating path,
        teardown included, and this is the only way to make it normal again.

        Gated on ``managed_service.adopt`` and nothing else. Reusing
        ``app.update`` (which provisioning takes) would mean every role that
        can book a database can also point one at somebody else's server, and
        adoption is the one operation that writes ownership onto a resource
        the platform cannot already prove is its own.
        """
        from astrolift_services.managed_resource_adoption import (
            AdoptionFailed,
            AdoptionRefused,
            AdoptionUnsupported,
            adopt_managed_resource,
        )

        svc = _managed_service_for_caller(input.id)
        if svc is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "managed service not found")
        # Adoption builds the provision spec from the stored config and binds
        # the adopted resource to it, so a config stored before #1921 carrying
        # a secret ref outside its owner's namespace is fixed first, not adopted.
        from astrolift_services.secret_ref_config import service_owner

        refused = _refuse_unscoped_config_secret_refs(
            svc.config,
            owner=service_owner(svc),
            cluster=svc.effective_cluster,
            code=ErrorCode.PRECONDITION.value,
        )
        if refused is not None:
            return refused

        request = info.context.request  # type: ignore[attr-defined]
        user = getattr(request, "user", None)
        try:
            outcome = adopt_managed_resource(
                svc=svc,
                resource_id=input.resource_id,
                reason=input.reason,
                acknowledged_prior_owner=input.acknowledged_prior_owner,
                actor=user,
            )
        except AdoptionUnsupported as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc))
        except AdoptionRefused as exc:
            # PRECONDITION rather than VALIDATION: the request is well formed,
            # and what is missing is authority over the resource -- either the
            # caller has to name the owner they are displacing, or there is
            # nothing here that may be adopted at all.
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        except AdoptionFailed as exc:
            return gql_failure(ErrorCode.INTERNAL.value, str(exc))

        record = outcome.record
        return gql_success(
            _ManagedResourceAdoptionPayload(
                id=GUID(str(record.guid)),
                managed_service_id=GUID(str(svc.guid)),
                cloud=record.cloud,
                resource_id=record.resource_id,
                surface=record.surface,
                classification=record.classification,
                prior_managed_by=record.prior_managed_by,
                prior_managed_service_id=record.prior_managed_service_id,
                prior_binding_id=record.prior_binding_id,
                prior_markers=record.prior_markers,
                stamped_markers=record.stamped_markers,
                acknowledged_prior_owner=record.acknowledged_prior_owner,
                reason=record.reason,
                actor_display=record.actor_display,
                status=record.status,
                adopted_at=record.created_at,
            )
        )

    # ---- Per-service quick actions (#401) -----------------------------
    #
    # The Settings landing surfaces a managed-services summary card with
    # a per-row dropdown of kind-specific actions:
    #   - postgres / redis / mysql  → revealManagedServiceConnection
    #   - object_store              → listManagedServiceObjects (query)
    #   - email                     → sendManagedServiceTestEmail
    #   - queue / topic             → managedServiceQueueDepth (query)
    #
    # Reveal mirrors #424's pattern: explicit mutation, decorated with
    # @mutation_audit + a sibling emit_audit row for the client IP.  No
    # plaintext leaves the platform — the envelope's `value` field is
    # always a `secret-ref:` or `placeholder:` shim.

    @strawberry.field
    @mutation_audit(
        action="managed_service.connection.reveal",
        extras=lambda result: (
            {
                "managed_service_id": str(result.data.managed_service_id),
                "kind": result.data.kind,
                "environment_name": result.data.environment_name,
                "key_count": len(result.data.keys),
            }
            if result.ok and result.data is not None
            else None
        ),
    )
    @requires_elevation(action_label="managed_service.connection.reveal")
    @require_permission(
        Permission.APP_READ,
        Permission.MANAGED_SERVICE_UPDATE,
        scope=managed_service_scope_by_guid(
            "input.managed_service_id",
            permissions=(
                Permission.APP_READ,
                Permission.MANAGED_SERVICE_UPDATE,
            ),
        ),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def reveal_managed_service_connection(
        self,
        info: Info,
        input: RevealManagedServiceConnectionInput,
    ) -> MutationResultType[ManagedServiceConnectionType]:
        """Disclose the connection envelope key set for one managed
        service (#401).

        Returns the stable env-var key set the workload sees at runtime
        for the service's kind, paired with the platform's
        `connection_secret_ref` pointer.  Plaintext values are NEVER
        returned — they live in the platform secrets backend (Vault /
        SecretsManager / GSM / KeyVault) and aren't reachable from this
        API surface.  Each `value` field is the opaque
        ``secret-ref:<ref>`` or ``placeholder:<note>`` shim that points
        the operator at where to fetch the value via the platform's
        secrets-backend client.

        Permission gate stacks `app.read` (caller can see the app) with
        `managed_service.update` (caller can disclose the pointer) so
        the surface matches the rest of the per-service action set.
        """
        # Lazy import to avoid circulars; the env_injection module is
        # the source of truth for the envelope key set per kind.
        from astrolift_manifest.env_injection import envelope_keys_for

        svc = (
            ManagedService.objects.select_related("app_environment", "registered_app")
            .filter(
                guid=str(input.managed_service_id),
                registered_app__organization_id=_caller_org_id(),
                deleted_at__isnull=True,
            )
            .first()
        )
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
                field="managedServiceId",
            )

        envelope = envelope_keys_for(svc.kind)
        if not envelope:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"kind {svc.kind!r} has no connection envelope to reveal; "
                    "reveal is only meaningful for kinds the platform injects "
                    "env vars for (postgres, redis, mysql, queue, topic, "
                    "object_store, email, etc.)"
                ),
                field="managedServiceId",
            )

        # Shape each key as ``secret-ref:<ref>`` when the workflow has
        # populated `connection_secret_ref`, else ``placeholder:pending``
        # so the UI can render a clear "not yet provisioned" hint
        # without us inventing a fake value.
        ref = svc.connection_secret_ref or ""
        if ref:
            value_for = lambda k: f"secret-ref:{ref}#{k}"  # noqa: E731
        else:
            value_for = lambda k: "placeholder:pending"  # noqa: E731

        keys = [
            ManagedServiceConnectionKeyType(
                key=k,
                value=value_for(k),
                # The handful of non-secret envelope keys are stable
                # config (region, prefix) — surface them as is_secret=
                # False so the UI doesn't mask them.
                is_secret=not _is_envelope_key_public(k),
            )
            for k in envelope
        ]

        # Sibling audit row carrying the client IP so the trail captures
        # the disclosure source (#424 pattern).  Done as a separate
        # emit_audit call so the IP isn't surfaced in the GraphQL
        # response (which would leak the caller's IP to a layered
        # proxy).
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.connection.reveal.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_READ.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "kind": svc.kind,
                    "name": svc.name,
                    "app_slug": svc.registered_app.slug,
                    "environment_name": svc.app_environment.name,
                    "key_count": len(keys),
                    "connection_secret_ref": ref,
                    "client_ip": ip,
                },
            )
        )

        # Stamp the cached "last operator action" surface so the
        # summary card can render "revealed N seconds ago" without
        # re-walking the audit log.
        now = timezone.now()
        svc.last_action_at = now
        svc.last_action_kind = "connection.reveal"
        svc.save(update_fields=["last_action_at", "last_action_kind", "updated_at", "version"])

        return gql_success(
            ManagedServiceConnectionType(
                managed_service_id=input.managed_service_id,
                kind=svc.kind,
                name=svc.name,
                environment_name=svc.app_environment.name,
                connection_secret_ref=ref,
                keys=keys,
                revealed_at=now,
            )
        )

    # ---- Model endpoint test prompt (#2064) ---------------------------

    @strawberry.field
    @mutation_audit(
        action="managed_service.test_prompt",
        extras=lambda result: (
            {"status": result.data.status, "latency_ms": result.data.latency_ms}
            if result.ok and result.data is not None
            else None
        ),
    )
    @require_permission(
        Permission.APP_UPDATE,
        scope=managed_service_scope_by_guid("input.managed_service_id", permissions=(Permission.APP_UPDATE,)),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def test_model_endpoint(
        self,
        info: Info,
        input: TestModelEndpointInput,
    ) -> MutationResultType[ModelEndpointTestType]:
        """Run one bounded chat completion against a hosted vLLM model,
        relayed through the cluster's in-cluster keep-alive agent (#2064).

        The control plane never reaches the model directly: the vLLM
        Service is ClusterIP-only behind a NetworkPolicy that admits only
        the owning app's namespace (plus, opt-in, the agent's own
        namespace/pod -- see ``k8s_native.managed.model_endpoint_vllm``).
        The agent resolves the model's API key from its own Kubernetes
        Secret and runs the completion in-cluster; this mutation only
        ever sees the reply text, latency, and token counts it reports
        back over the same heartbeat channel it already uses (#808).

        Gated on the same permission as ``updateManagedService``
        (``app.update``) because a test prompt spends the model's compute
        exactly like a config change spends its provisioning budget.
        """
        prompt = input.prompt.strip()
        if not prompt:
            return gql_failure(ErrorCode.VALIDATION.value, "prompt must not be empty", field="prompt")
        if len(prompt) > agent_test_jobs.MAX_PROMPT_CHARS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"prompt exceeds the {agent_test_jobs.MAX_PROMPT_CHARS}-character limit",
                field="prompt",
            )

        svc = live_model_prompt_service(input.managed_service_id)
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "managed service not found", field="managedServiceId"
            )
        state = prompt_readiness(svc)
        messages = {
            PromptReadinessState.UNSUPPORTED: "test prompt is only supported for vllm-hosted models",
            PromptReadinessState.INACTIVE: "wait for the managed service to become active",
            PromptReadinessState.UNAVAILABLE: "managed service has no active provisioning cluster",
            PromptReadinessState.UNKNOWN_HEARTBEAT: "this cluster has no connected agent",
            PromptReadinessState.STALE_HEARTBEAT: "this cluster has no connected agent",
            PromptReadinessState.UNCONFIGURED_RELAY: "this cluster has not opted the keep-alive agent into the model's NetworkPolicy",
            PromptReadinessState.UNCONFIGURED_MODEL: "managed service has no model configured",
        }
        if state != PromptReadinessState.READY:
            code = (
                ErrorCode.VALIDATION if state == PromptReadinessState.UNSUPPORTED else ErrorCode.PRECONDITION
            )
            return gql_failure(code.value, messages[state], field="managedServiceId")
        cluster = svc.app_environment.tenant_cluster
        model = svc.config["model"].strip()

        # Lazy: this schema module is imported by the schema-export command,
        # which runs without the provider plugins installed (see
        # ``core.cluster_management``'s identical rationale).
        from k8s_native.managed.model_endpoint_vllm import resolve_agent_test_target

        target = resolve_agent_test_target(
            organization_slug=svc.registered_app.organization.slug,
            app_slug=svc.registered_app.slug,
            environment_name=svc.app_environment.name,
            service_handle_hint=svc.name or svc.kind,
            model=model,
        )

        tenant = get_current_tenant()
        actor_user_id = tenant.actor_user_id if tenant else None

        try:
            agent_test_jobs.check_rate_limit(actor_user_id or 0)
        except agent_test_jobs.AgentTestRateLimited as exc:
            return gql_failure(ErrorCode.RATE_LIMITED.value, str(exc))

        try:
            # No result_url passed: the agent derives its own callback
            # endpoint from its configured heartbeat URL, never from
            # anything the control plane sends it (#2064 security review).
            job_id = agent_test_jobs.enqueue(
                cluster_guid=str(cluster.guid),
                managed_service_guid=str(svc.guid),
                prompt=prompt,
                model=target.model,
                base_url=target.base_url,
                secret_namespace=target.api_key_secret_namespace,
                secret_name=target.api_key_secret_name,
                secret_key=target.api_key_secret_key,
                requested_by_user_id=actor_user_id,
            )
        except agent_test_jobs.AgentTestConflict as exc:
            return gql_failure(ErrorCode.CONFLICT.value, str(exc))

        job = agent_test_jobs.await_result(
            job_id, heartbeat_interval_seconds=cluster.heartbeat_interval_seconds
        )

        svc.last_action_at = timezone.now()
        svc.last_action_kind = "test_prompt"
        svc.save(update_fields=["last_action_at", "last_action_kind", "updated_at", "version"])

        if job is None:
            return gql_success(
                ModelEndpointTestType(
                    status="timed_out",
                    reply="",
                    latency_ms=None,
                    prompt_tokens=None,
                    completion_tokens=None,
                    total_tokens=None,
                    error=(
                        "the cluster agent did not respond in time; it may be offline "
                        "or unable to reach the model"
                    ),
                )
            )
        return gql_success(
            ModelEndpointTestType(
                status="succeeded" if job["status"] == agent_test_jobs.SUCCEEDED else "failed",
                reply=job["reply"],
                latency_ms=job["latency_ms"],
                prompt_tokens=job["prompt_tokens"],
                completion_tokens=job["completion_tokens"],
                total_tokens=job["total_tokens"],
                error=job["error"],
            )
        )
