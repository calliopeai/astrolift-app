"""Audited current-admin controls for retained node/platform observations."""

from dataclasses import asdict

import strawberry
from strawberry.types import Info

from astrolift_clusters import nvke_install_plan_node_platforms as artifacts
from astrolift_clusters.schema.nvke import nvke_errors
from astrolift_clusters.schema.nvke_install_plan_immutable import (
    NvkeOperatorInstallPlanImmutableReviewInput,
    NvkeOperatorInstallPlanImmutableSourceInput,
)
from astrolift_clusters.schema.nvke_install_plans import _request_text
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.input
class PrepareNvkeOperatorInstallPlanNodePlatformsInput(NvkeOperatorInstallPlanImmutableSourceInput):
    request_text: str


@strawberry.input
class NvkeOperatorInstallPlanNodePlatformsReviewInput(NvkeOperatorInstallPlanImmutableReviewInput):
    pass


@strawberry.type
class NvkeOperatorInstallPlanNodePlatformsType:
    id: GUID
    version: int
    status: str
    plan_id: GUID
    plan_version: int
    plan_digest: str
    operation_id: GUID
    operation_version: int
    site_id: GUID
    site_version: int
    authority_id: GUID
    authority_version: int
    chart_artifact_id: GUID
    chart_artifact_version: int
    chart_artifact_digest: str
    tree_id: GUID
    tree_version: int
    tree_digest: str
    workload_id: GUID
    workload_version: int
    workload_digest: str
    binding_id: GUID
    binding_version: int
    binding_digest: str
    artifact_digest: str
    source_digest: str
    capture_digest: str
    observed_at: str
    execution_state: str
    continuation_authorized: bool
    platform_digest: str
    node_count: int
    content_count: int
    image_reference_count: int
    pair_count: int
    issue_count: int


def artifact_type(data):
    value = dict(data)
    metadata = value.pop("metadata")
    fields = {
        "artifact_digest": "artifactDigest",
        "source_digest": "sourceDigest",
        "capture_digest": "captureDigest",
        "platform_digest": "platformDigest",
        "observed_at": "observedAt",
        "node_count": "nodeCount",
        "content_count": "contentCount",
        "image_reference_count": "imageReferenceCount",
        "pair_count": "pairCount",
        "issue_count": "issueCount",
        "execution_state": "executionState",
        "continuation_authorized": "continuationAuthorized",
    }
    return NvkeOperatorInstallPlanNodePlatformsType(
        **value, **{field: metadata[key] for field, key in fields.items()}
    )


@strawberry.type
class NvkeInstallPlanNodePlatformsMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_node_platforms_prepare",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def prepare_nvke_operator_install_plan_node_platforms(
        self, info: Info, input: PrepareNvkeOperatorInstallPlanNodePlatformsInput
    ) -> MutationResultType[NvkeOperatorInstallPlanNodePlatformsType]:
        values = asdict(input)
        body = _request_text(values.pop("request_text"))
        result = artifacts.prepare(body=body, **values)
        return success(artifact_type(result))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_node_platforms_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install_plan_node_platforms(
        self, info: Info, input: NvkeOperatorInstallPlanNodePlatformsReviewInput
    ) -> MutationResultType[NvkeOperatorInstallPlanNodePlatformsType]:
        values = asdict(input)
        result = artifacts.read(**values)
        return success(artifact_type(result))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_node_platforms_recover_preparation",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def recover_nvke_operator_install_plan_node_platforms_preparation(
        self, info: Info, input: NvkeOperatorInstallPlanNodePlatformsReviewInput
    ) -> MutationResultType[NvkeOperatorInstallPlanNodePlatformsType]:
        values = asdict(input)
        result = artifacts.recover_preparation(**values)
        return success(artifact_type(result))
