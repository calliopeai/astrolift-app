"""Authoritative operation facts for ABAC, confined to the active organization."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from core.scope_args import read_arg, read_guid
from core.tenancy import get_current_tenant


@dataclasses.dataclass(frozen=True)
class OperationContext:
    environment: str | None = None
    region: str | None = None
    approvals: int | None = None

    def attributes(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


OperationLoader = Callable[[dict[str, Any]], tuple[OperationContext, ...]]
UNKNOWN = (OperationContext(),)


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def environment_context(environment, *, approvals: int | None = 0) -> OperationContext:
    if environment is None:
        return OperationContext(approvals=approvals)
    return OperationContext(
        environment=environment.name,
        region=environment.tenant_cluster.region or None,
        approvals=approvals,
    )


def named_environment(
    app_field: str = "input.app_slug",
    environment_field: str = "input.environment_name",
    *,
    all_if_absent: bool = False,
) -> OperationLoader:
    def load(args):
        from astrolift_lifecycle.models import AppEnvironment

        org_id, slug = _org_id(), read_arg(args, app_field)
        if org_id is None or not slug:
            return UNKNOWN
        environments = AppEnvironment.objects.filter(
            registered_app__organization_id=org_id,
            registered_app__slug=str(slug),
            registered_app__deleted_at__isnull=True,
            deleted_at__isnull=True,
        ).select_related("tenant_cluster")
        name = read_arg(args, environment_field)
        if name:
            environments = environments.filter(name=str(name))
        elif not all_if_absent:
            return UNKNOWN
        return tuple(environment_context(env) for env in environments.order_by("pk")) or UNKNOWN

    return load


def row_operation(
    model_label: str,
    field: str,
    *,
    environment_path: str = "app_environment",
    app_path: str = "registered_app",
) -> OperationLoader:
    def load(args):
        from django.apps import apps

        org_id, guid = _org_id(), read_guid(args, field)
        if org_id is None or guid is None:
            return UNKNOWN
        model = apps.get_model(model_label)
        row = (
            model.objects.filter(
                guid=guid, deleted_at__isnull=True, **{f"{app_path}__organization_id": org_id}
            )
            .select_related(
                "tenant_cluster" if environment_path == "self" else f"{environment_path}__tenant_cluster"
            )
            .first()
        )
        if row is None:
            return UNKNOWN
        environment = row if environment_path == "self" else getattr(row, environment_path)
        approvals = deployment_approval_count(row) if model_label == "astrolift_lifecycle.Deployment" else 0
        return (environment_context(environment, approvals=approvals),)

    return load


def environment_operation(field: str = "input.id") -> OperationLoader:
    return row_operation("astrolift_lifecycle.AppEnvironment", field, environment_path="self")


def deployment_operation(field: str = "input.id") -> OperationLoader:
    return row_operation("astrolift_lifecycle.Deployment", field)


def deployment_approval_count(deployment) -> int:
    """Only distinct durable human identities establish min_approvers."""
    return (
        deployment.approval_votes.filter(voter_user_id__isnull=False)
        .values("voter_user_id")
        .distinct()
        .count()
    )


def workload_operation(
    field: str = "input.workload_id", environment_field: str = "input.environment_id"
) -> OperationLoader:
    def load(args):
        from astrolift_lifecycle.services.k8s_ops import _primary_environment_for_workload
        from astrolift_registry.models import Workload

        org_id, guid = _org_id(), read_guid(args, field)
        if org_id is None or guid is None:
            return UNKNOWN
        workload = (
            Workload.objects.filter(
                guid=guid, registered_app__organization_id=org_id, deleted_at__isnull=True
            )
            .select_related("registered_app")
            .first()
        )
        if workload is None:
            return UNKNOWN
        if read_arg(args, environment_field) is not None:
            from astrolift_lifecycle.models import AppEnvironment

            environment_guid = read_guid(args, environment_field)
            environment = (
                AppEnvironment.objects.filter(guid=environment_guid, registered_app=workload.registered_app)
                .select_related("tenant_cluster")
                .first()
                if environment_guid is not None
                else None
            )
        else:
            environment = _primary_environment_for_workload(workload)
        return (environment_context(environment),)

    return load


def secret_write_operation(args: dict[str, Any]) -> tuple[OperationContext, ...]:
    operation = read_arg(args, "input.op")
    field = "input.environment_name" if operation in (None, "attach_bundle", "detach_bundle") else "_absent"
    return named_environment(environment_field=field, all_if_absent=True)(args)


def bulk_deployment_operation(args: dict[str, Any]) -> tuple[OperationContext, ...]:
    identities = read_arg(args, "input.deployment_ids") or []
    return (
        tuple(context for guid in identities for context in deployment_operation("id")({"id": guid}))
        or UNKNOWN
    )


def agent_region_operation(args: dict[str, Any]) -> tuple[OperationContext, ...]:
    from astrolift_agents.services.agent_cluster import NoAgentClusterError, resolve_agent_cluster
    from astrolift_identity.models import Organization

    org_id = _org_id()
    organization = Organization.objects.filter(pk=org_id).first() if org_id is not None else None
    if organization is None:
        return UNKNOWN
    try:
        cluster = resolve_agent_cluster(organization)
    except NoAgentClusterError:
        return UNKNOWN
    return (OperationContext(region=getattr(cluster, "region", None) or None, approvals=0),)


def agent_task_operation(field: str = "id") -> OperationLoader:
    def load(args):
        from astrolift_agents.models import AgentTask

        org_id, guid = _org_id(), read_guid(args, field)
        if org_id is None or guid is None:
            return UNKNOWN
        task = (
            AgentTask.objects.filter(guid=guid, organization_id=org_id, deleted_at__isnull=True)
            .select_related("agent_run__app_environment__tenant_cluster")
            .first()
        )
        if task is None:
            return UNKNOWN
        return (agent_task_contexts([task], org_id)[task.pk],)

    return load


def agent_task_contexts(tasks, org_id):
    """Batch the same frozen dispatch facts for object gates and collections."""
    from django.db.models import Q

    from astrolift_clusters.models import TenantCluster

    targets = {}
    for task in tasks:
        target = task.dispatch_target if isinstance(task.dispatch_target, dict) else {}
        if target.get("cluster_guid"):
            targets[task.pk] = read_guid({"guid": target["cluster_guid"]}, "guid")
    clusters = {
        str(cluster.guid): cluster
        for cluster in TenantCluster.objects.filter(
            Q(organization_id=org_id) | Q(organization_id__isnull=True),
            guid__in={guid for guid in targets.values() if guid is not None},
        )
    }
    contexts = {}
    default = None
    for task in tasks:
        context = (
            environment_context(task.agent_run.app_environment)
            if task.agent_run_id and task.agent_run.app_environment_id
            else OperationContext(approvals=0)
        )
        if task.pk in targets:
            cluster = clusters.get(str(targets[task.pk]))
            context = dataclasses.replace(context, region=cluster.region or None) if cluster else UNKNOWN[0]
        elif context.environment is None:
            if default is None:
                default = agent_region_operation({})[0]
            context = default
        contexts[task.pk] = context
    return contexts


def migration_operation(args: dict[str, Any]) -> tuple[OperationContext, ...]:
    from django.db.models import Q

    from astrolift_clusters.models import TenantCluster

    contexts = environment_operation("input.app_environment_id")(args)
    guid, org_id = read_guid(args, "input.target_cluster_id"), _org_id()
    if guid is None or org_id is None:
        return UNKNOWN
    target = TenantCluster.objects.filter(
        Q(organization_id=org_id) | Q(organization_id__isnull=True), guid=guid, deleted_at__isnull=True
    ).first()
    if target is None:
        return UNKNOWN
    return (*contexts, *(dataclasses.replace(context, region=target.region or None) for context in contexts))


def preview_creation_operation(args: dict[str, Any]) -> tuple[OperationContext, ...]:
    from astrolift_lifecycle.schema.mutations.helpers import _slugify_branch
    from astrolift_registry.models import RegisteredApp

    org_id, slug = _org_id(), read_arg(args, "input.app_slug")
    if org_id is None or not slug:
        return UNKNOWN
    app = (
        RegisteredApp.objects.filter(slug=slug, organization_id=org_id, deleted_at__isnull=True)
        .select_related("default_tenant_cluster")
        .first()
    )
    if app is None or app.default_tenant_cluster is None:
        return UNKNOWN
    name = str(read_arg(args, "input.environment_name") or "").strip()
    branch = _slugify_branch(str(read_arg(args, "input.branch") or "").strip())
    return (
        OperationContext(
            environment=name or f"preview-{branch}",
            region=app.default_tenant_cluster.region or None,
            approvals=0,
        ),
    )


def bundle_operation(field: str) -> OperationLoader:
    def load(args):
        from astrolift_services.models import SecretBundle

        org_id, guid = _org_id(), read_guid(args, field)
        if org_id is None or guid is None:
            return UNKNOWN
        bundle = SecretBundle.objects.filter(
            guid=guid, project__organization_id=org_id, deleted_at__isnull=True
        ).first()
        if bundle is None:
            return UNKNOWN
        return (
            tuple(
                environment_context(ref.app_environment)
                for ref in bundle.app_refs.filter(
                    registered_app__organization_id=org_id,
                    deleted_at__isnull=True,
                    app_environment__deleted_at__isnull=True,
                ).select_related("app_environment__tenant_cluster")
            )
            or UNKNOWN
        )

    return load


def reveal_secret_operation(args: dict[str, Any]) -> tuple[OperationContext, ...]:
    secret_id = str(read_arg(args, "input.secret_id") or "")
    parts = secret_id.split(":", 2)
    if len(parts) != 3 or not all(parts):
        return UNKNOWN
    return named_environment("input.app_slug", "environment")({**args, "environment": parts[1]})


def secret_proposal_operation(field: str = "input.proposal_id") -> OperationLoader:
    def load(args):
        from astrolift_services.models import SecretChangeApproval, SecretChangeProposal

        org_id, guid = _org_id(), read_guid(args, field)
        if org_id is None or guid is None:
            return UNKNOWN
        proposal = (
            SecretChangeProposal.objects.filter(
                guid=guid, registered_app__organization_id=org_id, deleted_at__isnull=True
            )
            .select_related("app_environment__tenant_cluster", "registered_app")
            .defer("payload", "payload_diff", "apply_error")
            .first()
        )
        if proposal is None:
            return UNKNOWN
        count = (
            proposal.approvals.filter(
                decision=SecretChangeApproval.Decision.APPROVED,
                deleted_at__isnull=True,
                approver_id__isnull=False,
            )
            .values("approver_id")
            .distinct()
            .count()
        )
        if proposal.app_environment_id and proposal.op in ("attach_bundle", "detach_bundle"):
            return (environment_context(proposal.app_environment, approvals=count),)
        contexts = named_environment("app_slug", "environment", all_if_absent=True)(
            {"app_slug": proposal.registered_app.slug}
        )
        return tuple(dataclasses.replace(context, approvals=count) for context in contexts)

    return load


def workflow_run_context(run) -> OperationContext:
    from workflows.models import WorkflowStageExecution

    voters = WorkflowStageExecution.objects.filter(
        workflow_run=run,
        status="completed",
        stage__kind="human_gate",
        deleted_at__isnull=True,
        output__human_gate__decision="approved",
    ).values_list("output__human_gate__decided_by_user_id", flat=True)
    approvals = len(
        {
            user_id
            for user_id in voters
            if isinstance(user_id, int) and not isinstance(user_id, bool) and user_id > 0
        }
    )
    if run.app_environment_id:
        return environment_context(run.app_environment, approvals=approvals)
    # Agent definitions have no app environment. Their initial dispatch uses
    # the same managed cluster as the agent secret store and run entry point.
    context = agent_region_operation({})[0]
    return dataclasses.replace(context, approvals=approvals)


def workflow_operation(workflow_id: str, run_id: str | None = None) -> OperationContext:
    from astrolift_operations.models import WorkflowRun

    org_id = _org_id()
    if org_id is None:
        return OperationContext()
    rows = WorkflowRun.objects.filter(
        workflow_id=workflow_id, organization_id=org_id, deleted_at__isnull=True
    )
    if run_id is not None:
        rows = rows.filter(run_id=run_id)
    run = rows.select_related("app_environment__tenant_cluster").order_by("-pk").first()
    return workflow_run_context(run) if run is not None else OperationContext()


def workflow_id_operation(field: str = "workflow_id", run_field: str | None = None) -> OperationLoader:
    def load(args):
        run_id = read_arg(args, run_field) if run_field else None
        return (workflow_operation(str(read_arg(args, field) or ""), run_id),)

    return load


def execution_operation(args: dict[str, Any]) -> tuple[OperationContext, ...]:
    from astrolift_workflows.execution_controls import find_execution

    run = find_execution(_org_id(), str(read_arg(args, "execution_id") or ""))
    return (workflow_run_context(run),) if run is not None else UNKNOWN


def instance_operation(field: str = "instance_id") -> OperationLoader:
    def load(args):
        from workflows.models import WorkflowInstance

        raw = str(read_arg(args, field) or "")
        org_id = _org_id()
        if not raw.isdigit() or org_id is None:
            return UNKNOWN
        instance = WorkflowInstance.objects.filter(pk=int(raw), organization_id=org_id).first()
        if instance is None:
            return UNKNOWN
        return (workflow_operation(instance.temporal_workflow_id),)

    return load


def managed_service_operation(field: str = "input.id") -> OperationLoader:
    def load(args):
        from django.db.models import Q

        from astrolift_services.models import ManagedService

        guid, org_id = read_guid(args, field), _org_id()
        if guid is None or org_id is None:
            return UNKNOWN
        service = (
            ManagedService.objects.filter(
                Q(registered_app__organization_id=org_id) | Q(project__organization_id=org_id),
                guid=guid,
                deleted_at__isnull=True,
            )
            .select_related("tenant_cluster", "app_environment__tenant_cluster")
            .first()
        )
        if service is None:
            return UNKNOWN
        if service.app_environment_id:
            return (environment_context(service.app_environment),)
        return (
            OperationContext(
                environment=service.effective_environment_name,
                region=service.tenant_cluster.region or None,
                approvals=0,
            ),
        )

    return load


def project_service_creation_operation(args: dict[str, Any]) -> tuple[OperationContext, ...]:
    from django.db.models import Q

    from astrolift_clusters.models import TenantCluster
    from astrolift_identity.models import Project

    org_id = _org_id()
    project_guid, cluster_guid = read_guid(args, "input.project_id"), read_guid(args, "input.cluster_id")
    if org_id is None or not project_guid or not cluster_guid:
        return UNKNOWN
    if not Project.objects.filter(
        guid=project_guid, organization_id=org_id, deleted_at__isnull=True
    ).exists():
        return UNKNOWN
    cluster = TenantCluster.objects.filter(
        Q(organization_id=org_id) | Q(organization_id__isnull=True),
        guid=cluster_guid,
        deleted_at__isnull=True,
    ).first()
    if cluster is None:
        return UNKNOWN
    return (
        OperationContext(
            environment=str(read_arg(args, "input.environment_name") or "default"),
            region=cluster.region or None,
            approvals=0,
        ),
    )
