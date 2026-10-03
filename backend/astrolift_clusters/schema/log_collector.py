"""Metadata-only review and original-request recovery for the collector."""

from datetime import datetime
from enum import Enum
from typing import cast

import strawberry
from django.utils import timezone
from strawberry.scalars import JSON
from strawberry.types import Info

from astrolift_clusters import agent_install, log_collector
from astrolift_clusters.scopes import cluster_catalog_org_scope, cluster_org_scope
from astrolift_graphql import GUID, MutationResultType, failure, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.enum
class ClusterLogCollectorStatus(Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    INSTALLING = "installing"
    READINESS_PENDING = "readiness_pending"
    READER_GRANT_PENDING = "reader_grant_pending"
    INGESTION_PENDING = "ingestion_pending"
    PROBE_DELETION_PENDING = "probe_deletion_pending"
    POST_LOSS_READ_PENDING = "post_loss_read_pending"
    UNCERTAIN = "uncertain"
    REFUSED = "refused"
    ACTIVATED = "activated"


@strawberry.input
class InstallClusterLogCollectorInput:
    cluster_id: GUID  # type: ignore[valid-type]
    request_id: str
    expected_version: int
    expected_source: str
    retention_days: int = 30


@strawberry.type
class AstroliftClusterLogCollectorReview:
    cluster_id: GUID  # type: ignore[valid-type]
    version: int
    source: str
    retention_days: int
    supported: bool
    refusal_code: str
    message: str
    policy: JSON
    reader_policy: JSON


@strawberry.type
class AstroliftClusterLogCollectorOperation:
    id: GUID  # type: ignore[valid-type]
    cluster_id: GUID  # type: ignore[valid-type]
    request_id: str
    expected_version: int
    expected_source: str
    retention_days: int
    status: ClusterLogCollectorStatus
    stage: str
    retryable: bool
    cleanup_pending: bool
    coverage: str
    workflow_id: str
    deadline: datetime
    post_loss_verified_at: datetime | None
    activated_at: datetime | None
    activated_cluster_version: int | None
    error_code: str
    error_message: str
    reader_policy: JSON
    created_at: datetime
    updated_at: datetime


def operation_to_type(row):
    from aws.cloudwatch_collector import CollectorStage

    prepared = row.checkpoints.get("prepared")
    reader_policy = CollectorStage(**prepared).reader_policy() if prepared else {}
    return AstroliftClusterLogCollectorOperation(
        id=GUID(str(row.guid)),
        cluster_id=GUID(str(row.tenant_cluster.guid)),
        request_id=str(row.request_id),
        expected_version=row.expected_version,
        expected_source=row.expected_source,
        retention_days=row.retention_days,
        status=ClusterLogCollectorStatus(row.status),
        stage=row.stage,
        retryable=not row.terminal and timezone.now() < row.deadline,
        cleanup_pending=row.cleanup_pending,
        coverage=row.coverage,
        workflow_id=row.workflow_id,
        deadline=row.deadline,
        post_loss_verified_at=row.post_loss_verified_at,
        activated_at=row.activated_at,
        activated_cluster_version=row.activated_cluster_version,
        error_code=row.error_code,
        error_message=row.error_message,
        reader_policy=reader_policy,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@strawberry.type
class ClusterLogCollectorQuery:
    @strawberry.field
    @require_permission(Permission.CLUSTER_MANAGE, scope=cluster_org_scope(Permission.CLUSTER_MANAGE))
    @tenant_scoped()
    def astrolift_cluster_log_collector_review(
        self,
        info: Info,
        cluster_id: GUID,
        retention_days: int = 30,  # type: ignore[valid-type]
    ) -> AstroliftClusterLogCollectorReview:
        result = log_collector.review_collector(str(cluster_id), retention_days)
        return AstroliftClusterLogCollectorReview(
            cluster_id=GUID(str(result["cluster"].guid)),
            version=result["cluster"].version,
            source=result["source"],
            retention_days=retention_days,
            supported=result["supported"],
            refusal_code=result["refusal_code"],
            message=result["message"],
            policy=result["policy"],
            reader_policy=result["reader_policy"],
        )

    @strawberry.field
    @require_permission(Permission.CLUSTER_MANAGE, scope=cluster_catalog_org_scope(Permission.CLUSTER_MANAGE))
    @tenant_scoped()
    def astrolift_cluster_log_collector_operation(
        self,
        info: Info,
        operation_id: GUID,  # type: ignore[valid-type]
    ) -> AstroliftClusterLogCollectorOperation:
        return operation_to_type(log_collector.read_collector(str(operation_id)))


@strawberry.type
class ClusterLogCollectorMutation:
    @strawberry.field
    @mutation_audit(
        action="cluster.install_log_collector",
        target=lambda root, info, input: ("cluster", str(input.cluster_id)),
    )
    @require_permission(
        Permission.CLUSTER_MANAGE, scope=cluster_org_scope(Permission.CLUSTER_MANAGE, "input.cluster_id")
    )
    @tenant_scoped()
    def astrolift_install_cluster_log_collector(
        self, info: Info, input: InstallClusterLogCollectorInput
    ) -> MutationResultType[AstroliftClusterLogCollectorOperation]:
        try:
            row = log_collector.reserve_collector(
                cluster_id=str(input.cluster_id),
                request_id=input.request_id,
                expected_version=input.expected_version,
                expected_source=input.expected_source,
                retention_days=input.retention_days,
            )
            row.refresh_from_db()
        except (log_collector.CollectorError, agent_install.AgentInstallError) as exc:
            return cast(
                MutationResultType[AstroliftClusterLogCollectorOperation], failure(exc.code, str(exc))
            )
        return success(operation_to_type(row))
