"""Immutable review facts for a workload's exact environment mapping."""

from dataclasses import dataclass

from astrolift_lifecycle.visibility import cluster_owned_and_live
from core.optimistic import check_version_match


@dataclass(frozen=True)
class WorkloadActionTarget:
    workload_id: str
    workload_version: int
    app_id: str
    app_version: int
    environment_id: str
    environment_name: str
    environment_version: int
    cluster_id: str
    cluster_version: int
    namespace: str


def action_target(workload, environment) -> WorkloadActionTarget | None:
    from core.app_deploy import namespace_for_environment

    app = workload.registered_app
    if (
        environment is None
        or environment.deleted_at is not None
        or environment.registered_app_id != app.pk
        or not cluster_owned_and_live(environment.tenant_cluster, app.organization_id)
    ):
        return None
    environment.registered_app = app
    return WorkloadActionTarget(
        workload_id=str(workload.guid),
        workload_version=int(workload.version or 0),
        app_id=str(app.guid),
        app_version=int(app.version or 0),
        environment_id=str(environment.guid),
        environment_name=environment.name,
        environment_version=int(environment.version or 0),
        cluster_id=str(environment.tenant_cluster.guid),
        cluster_version=int(environment.tenant_cluster.version or 0),
        namespace=namespace_for_environment(environment),
    )


def check_target_match(workload, environment, input, *, if_match_version):
    from astrolift_graphql import failure
    from core.mutations import ErrorCode

    explicit = input.environment_id is not None
    supplied = (
        input.if_match_environment_version,
        input.expected_cluster_id,
        input.if_match_cluster_version,
        input.if_match_app_version,
        input.expected_namespace,
    )
    if not explicit and any(value is not None for value in supplied):
        return failure(ErrorCode.VALIDATION.value, "environmentId is required for target preconditions")
    if explicit and (if_match_version is None or any(value is None for value in supplied)):
        return failure(
            ErrorCode.VALIDATION.value,
            "Explicit targets require workload, app, environment and cluster versions plus expectedClusterId and expectedNamespace",
        )
    mismatch = check_version_match(workload, if_match_version=if_match_version, kind="Workload")
    if mismatch is not None:
        return mismatch
    target = action_target(workload, environment)
    if target is None:
        return failure(ErrorCode.PRECONDITION.value, "workload environment target is unavailable")
    if not explicit:
        return None
    if str(input.expected_cluster_id) != target.cluster_id:
        return failure(ErrorCode.PRECONDITION.value, "reviewed environment cluster mapping has changed")
    if input.expected_namespace != target.namespace:
        return failure(ErrorCode.PRECONDITION.value, "reviewed environment namespace mapping has changed")
    for model, version, kind in (
        (workload.registered_app, input.if_match_app_version, "RegisteredApp"),
        (environment, input.if_match_environment_version, "AppEnvironment"),
        (environment.tenant_cluster, input.if_match_cluster_version, "TenantCluster"),
    ):
        mismatch = check_version_match(model, if_match_version=version, kind=kind)
        if mismatch is not None:
            return mismatch
    return None


def action_audit_target(*args, **kwargs):
    input = kwargs.get("input") or (args[2] if len(args) > 2 else None)
    return ("WORKLOAD", str(input.workload_id)) if input is not None else None


def action_audit_extras(result):
    data = getattr(result, "data", None)
    target = getattr(data, "target", None)
    if not getattr(result, "ok", False) or target is None:
        return None
    return {
        "operation_id": str(data.operation_id),
        "accepted": data.accepted,
        "completed": data.completed,
        "workload_id": str(target.workload_id),
        "reviewed_workload_version": target.workload_version,
        "resulting_workload_version": data.workload_version,
        "app_id": str(target.app_id),
        "app_version": target.app_version,
        "environment_id": str(target.environment_id),
        "environment_version": target.environment_version,
        "cluster_id": str(target.cluster_id),
        "cluster_version": target.cluster_version,
        "namespace": target.namespace,
    }
