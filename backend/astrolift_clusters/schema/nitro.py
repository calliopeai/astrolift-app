"""Read-only Nitro evidence and explicit, version-bound per-cluster task opt-in."""

from datetime import datetime

import strawberry
from django.db import transaction
from django.db.models import Q
from strawberry.scalars import JSON
from strawberry.types import Info

from astrolift_clusters import nitro
from astrolift_clusters.models import TenantCluster
from astrolift_clusters.schema.mutations import _require_operator_for_shared
from astrolift_clusters.scopes import cluster_org_scope
from astrolift_graphql import GUID, MutationResultType, failure, success
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class AstroliftNitroReadinessCheck:
    code: str
    state: str
    message: str
    evidence: JSON


@strawberry.type
class AstroliftNitroTaskMode:
    mode: str
    enabled: bool
    state: str
    checks: list[AstroliftNitroReadinessCheck]


@strawberry.type
class AstroliftNitroTaskReadiness:
    cluster_id: GUID
    version: int
    source_token: str
    checked_at: datetime
    namespace: str
    source: JSON
    modes: list[AstroliftNitroTaskMode]


@strawberry.input
class ConfigureNitroTasksInput:
    cluster_id: GUID
    expected_version: int
    expected_source: str
    task_modes: list[str]


def visible_cluster(info, cluster_id, *, lock=False):
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return None
    rows = TenantCluster.objects.select_related("organization", "provider_plugin")
    if lock:
        rows = rows.select_for_update(of=("self",))
    cluster = rows.filter(
        Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
        guid=str(cluster_id),
        deleted_at__isnull=True,
    ).first()
    _require_operator_for_shared(info, cluster, Permission.CLUSTER_MANAGE)
    return cluster


def readiness_type(report):
    return AstroliftNitroTaskReadiness(
        cluster_id=GUID(report["cluster_id"]),
        version=report["version"],
        source_token=report["source_token"],
        checked_at=datetime.fromisoformat(report["checked_at"]),
        namespace=report["namespace"],
        source=report["source"],
        modes=[
            AstroliftNitroTaskMode(
                mode=mode,
                enabled=mode in report["enabled_modes"],
                state=value["state"],
                checks=[AstroliftNitroReadinessCheck(**row) for row in value["checks"]],
            )
            for mode, value in report["modes"].items()
        ],
    )


@strawberry.type
class NitroTasksQuery:
    @strawberry.field
    @require_permission(Permission.CLUSTER_MANAGE, scope=cluster_org_scope(Permission.CLUSTER_MANAGE))
    @tenant_scoped()
    def astrolift_nitro_task_readiness(
        self, info: Info, cluster_id: GUID
    ) -> MutationResultType[AstroliftNitroTaskReadiness]:
        cluster = visible_cluster(info, cluster_id)
        if cluster is None:
            return failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        return success(readiness_type(nitro.review(cluster)))


@strawberry.type
class NitroTasksMutation:
    @strawberry.field
    @mutation_audit(action="cluster.configure_nitro_tasks")
    @require_permission(
        Permission.CLUSTER_MANAGE, scope=cluster_org_scope(Permission.CLUSTER_MANAGE, "input.cluster_id")
    )
    @tenant_scoped()
    def configure_nitro_tasks(
        self, info: Info, input: ConfigureNitroTasksInput
    ) -> MutationResultType[AstroliftNitroTaskReadiness]:
        cluster = visible_cluster(info, input.cluster_id)
        if cluster is None:
            return failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        if cluster.version != input.expected_version:
            return failure(ErrorCode.VERSION_MISMATCH.value, "Cluster changed; review Nitro readiness again.")
        if len(set(input.task_modes)) != len(input.task_modes) or set(input.task_modes) - nitro.MODES:
            return failure(
                ErrorCode.VALIDATION.value,
                "Task modes must be unique isolated/confidential selections.",
                field="taskModes",
            )
        report = nitro.review(cluster)
        if report["source_token"] != input.expected_source:
            return failure(
                ErrorCode.PRECONDITION.value, "Nitro configuration changed; refresh readiness before saving."
            )
        added = set(input.task_modes) - set(nitro.task_modes(cluster))
        if any(report["modes"].get(mode, {}).get("state") != "ready" for mode in added):
            return failure(
                ErrorCode.PRECONDITION.value,
                "The selected Nitro task prerequisites are not ready. No infrastructure was changed.",
                field="taskModes",
            )
        with transaction.atomic():
            current = visible_cluster(info, input.cluster_id, lock=True)
            if current is None or current.version != input.expected_version:
                return failure(
                    ErrorCode.VERSION_MISMATCH.value, "Cluster changed; review Nitro readiness again."
                )
            from astrolift_services.cluster_retirement import recheck_cluster_authority

            recheck_cluster_authority(current, Permission.CLUSTER_MANAGE)
            values = dict(current.provider_config or {})
            existing = values.get("nitro_sandboxes")
            block = dict(existing) if isinstance(existing, dict) else {}
            block["task_modes"] = sorted(input.task_modes)
            values["nitro_sandboxes"] = block
            current.provider_config = values
            current.save(update_fields=["provider_config", "updated_at", "version"])
        report.update(
            version=current.version,
            enabled_modes=nitro.task_modes(current),
            source_token=nitro.source_token(current, report["namespace"], report["source"]),
        )
        return success(readiness_type(report))
