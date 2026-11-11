"""Audited current-admin review of historical Pod/node correspondence."""

from dataclasses import asdict

import strawberry
from strawberry.types import Info

from astrolift_clusters import nvke_install_plan_pull_runtime as artifacts
from astrolift_clusters.schema import nvke_install_plan_node_platforms as nodes
from astrolift_clusters.schema import nvke_install_plan_pull_secret_analysis as analyses
from astrolift_clusters.schema.nvke import nvke_errors
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.input
class NvkeOperatorInstallPlanPullRuntimeReviewInput(
    analyses.NvkeOperatorInstallPlanPullSecretAnalysisSourceInput
):
    analysis_id: GUID
    expected_analysis_version: int
    analysis_digest: str
    node_platform_id: GUID
    expected_node_platform_version: int
    node_platform_digest: str


@strawberry.type
class NvkeOperatorInstallPlanPullRuntimeType:
    analysis: analyses.NvkeOperatorInstallPlanPullSecretAnalysisType
    node_platforms: nodes.NvkeOperatorInstallPlanNodePlatformsType
    correspondence_digest: str
    image_use_count: int
    node_count: int
    source_observed_at: str
    node_observed_at: str
    execution_state: str
    continuation_authorized: bool


def correspondence_type(data):
    summary = data["metadata"]
    fields = {
        "correspondence_digest": "correspondenceDigest",
        "image_use_count": "imageUseCount",
        "node_count": "nodeCount",
        "source_observed_at": "sourceObservedAt",
        "node_observed_at": "nodeObservedAt",
        "execution_state": "executionState",
        "continuation_authorized": "continuationAuthorized",
    }
    return NvkeOperatorInstallPlanPullRuntimeType(
        analysis=analyses.artifact_type(data["analysis"]),
        node_platforms=nodes.artifact_type(data["node_platforms"]),
        **{field: summary[key] for field, key in fields.items()},
    )


@strawberry.type
class NvkeInstallPlanPullRuntimeMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_pull_runtime_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install_plan_pull_runtime(
        self, info: Info, input: NvkeOperatorInstallPlanPullRuntimeReviewInput
    ) -> MutationResultType[NvkeOperatorInstallPlanPullRuntimeType]:
        return success(correspondence_type(artifacts.read(**asdict(input))))
