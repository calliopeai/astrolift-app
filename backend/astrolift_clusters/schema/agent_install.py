"""Metadata-only contract for server-owned keep-alive installation (#1696)."""

from datetime import datetime
from enum import Enum
from typing import cast

import strawberry
from strawberry.types import Info

from astrolift_clusters import agent_install
from astrolift_clusters.scopes import cluster_catalog_org_scope, cluster_org_scope
from astrolift_graphql import GUID, MutationResultType, failure, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.enum
class ClusterAgentInstallStatus(Enum):
    QUEUED = "queued"
    INSTALLING = "installing"
    AWAITING_HEARTBEAT = "awaiting_heartbeat"
    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    UNCERTAIN = "uncertain"


@strawberry.input
class InstallClusterAgentInput:
    cluster_id: GUID  # type: ignore[valid-type]  # Strawberry runtime scalar.
    expected_version: int
    expected_source: str
    request_id: str
    interval_seconds: int | None = None


@strawberry.type
class AstroliftClusterAgentInstallReview:
    cluster_id: GUID  # type: ignore[valid-type]  # Strawberry runtime scalar.
    version: int
    source: str


@strawberry.type
class AstroliftClusterAgentInstall:
    id: GUID  # type: ignore[valid-type]  # Strawberry runtime scalar.
    cluster_id: GUID  # type: ignore[valid-type]  # Strawberry runtime scalar.
    request_id: str
    status: ClusterAgentInstallStatus
    secret_confirmed: bool
    deployment_confirmed: bool
    heartbeat_confirmed: bool
    retryable: bool
    workflow_id: str
    error_code: str
    error_message: str
    created_at: datetime
    updated_at: datetime


def install_to_type(row):
    return AstroliftClusterAgentInstall(
        id=GUID(str(row.guid)),
        cluster_id=GUID(str(row.tenant_cluster.guid)),
        request_id=str(row.request_id),
        status=ClusterAgentInstallStatus(row.status),
        secret_confirmed=bool(row.secret_uid),
        deployment_confirmed=row.deployment_confirmed,
        heartbeat_confirmed=row.heartbeat_confirmed_at is not None,
        retryable=row.status in {"queued", "installing", "uncertain"},
        workflow_id=row.workflow_id,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@strawberry.type
class ClusterAgentInstallQuery:
    @strawberry.field
    @require_permission(Permission.CLUSTER_MANAGE, scope=cluster_org_scope(Permission.CLUSTER_MANAGE))
    @tenant_scoped()
    def astrolift_cluster_agent_install_review(
        self,
        info: Info,
        cluster_id: GUID,  # type: ignore[valid-type]  # Strawberry runtime scalar.
    ) -> AstroliftClusterAgentInstallReview:
        cluster, source = agent_install.review_install(str(cluster_id))
        return AstroliftClusterAgentInstallReview(
            cluster_id=GUID(str(cluster.guid)), version=cluster.version, source=source
        )

    @strawberry.field
    @require_permission(Permission.CLUSTER_MANAGE, scope=cluster_catalog_org_scope(Permission.CLUSTER_MANAGE))
    @tenant_scoped()
    def astrolift_cluster_agent_install(
        self,
        info: Info,
        install_id: GUID,  # type: ignore[valid-type]  # Strawberry runtime scalar.
    ) -> AstroliftClusterAgentInstall:
        return install_to_type(agent_install.read_install(str(install_id)))


@strawberry.type
class ClusterAgentInstallMutation:
    @strawberry.field
    @mutation_audit(
        action="cluster.install_agent", target=lambda root, info, input: ("cluster", str(input.cluster_id))
    )
    @require_permission(
        Permission.CLUSTER_MANAGE, scope=cluster_org_scope(Permission.CLUSTER_MANAGE, "input.cluster_id")
    )
    @tenant_scoped()
    def install_cluster_agent(
        self, info: Info, input: InstallClusterAgentInput
    ) -> MutationResultType[AstroliftClusterAgentInstall]:
        try:
            row = agent_install.reserve_install(
                cluster_id=str(input.cluster_id),
                expected_version=input.expected_version,
                expected_source=input.expected_source,
                request_id=input.request_id,
                interval_seconds=input.interval_seconds,
            )
            row = agent_install.dispatch_install(row)
        except agent_install.AgentInstallError as exc:
            return cast(MutationResultType[AstroliftClusterAgentInstall], failure(exc.code, str(exc)))
        return success(install_to_type(row))
