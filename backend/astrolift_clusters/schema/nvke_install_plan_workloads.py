"""Reviewed all-item workload inventory with no native execution admission."""

from dataclasses import asdict

import strawberry
from strawberry.types import Info

from astrolift_clusters import nvke_install_plan_workloads as workloads
from astrolift_clusters.schema.nvke import nvke_errors
from astrolift_clusters.schema.nvke_install_plan_trees import NvkeOperatorInstallPlanTreeSourceInput
from astrolift_clusters.schema.nvke_install_plans import _request_text
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.input
class NvkeOperatorInstallPlanWorkloadSourceInput(NvkeOperatorInstallPlanTreeSourceInput):
    tree_id: GUID
    expected_tree_version: int
    tree_digest: str


@strawberry.input
class PrepareNvkeOperatorInstallPlanWorkloadInput(NvkeOperatorInstallPlanWorkloadSourceInput):
    request_text: str


@strawberry.input
class NvkeOperatorInstallPlanWorkloadReviewInput(NvkeOperatorInstallPlanWorkloadSourceInput):
    artifact_id: GUID


@strawberry.type
class NvkeOperatorInstallPlanWorkloadType:
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
    artifact_digest: str
    source_digest: str
    capture_digest: str
    workload_digest: str
    item_count: int
    resource_count: int
    image_use_count: int
    image_reference_count: int
    issue_count: int
    observed_at: str
    execution_state: str
    continuation_authorized: bool


def workload_type(data):
    value = dict(data)
    metadata = value.pop("metadata")
    return NvkeOperatorInstallPlanWorkloadType(
        **value,
        artifact_digest=metadata["artifactDigest"],
        source_digest=metadata["sourceDigest"],
        capture_digest=metadata["captureDigest"],
        workload_digest=metadata["workloadDigest"],
        item_count=metadata["itemCount"],
        resource_count=metadata["resourceCount"],
        image_use_count=metadata["imageUseCount"],
        image_reference_count=metadata["imageReferenceCount"],
        issue_count=metadata["issueCount"],
        observed_at=metadata["observedAt"],
        execution_state=metadata["executionState"],
        continuation_authorized=metadata["continuationAuthorized"],
    )


@strawberry.type
class NvkeInstallPlanWorkloadMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_workload_prepare",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def prepare_nvke_operator_install_plan_workload(
        self,
        info: Info,
        input: PrepareNvkeOperatorInstallPlanWorkloadInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanWorkloadType]:
        values = asdict(input)
        body = _request_text(values.pop("request_text"))
        return success(workload_type(workloads.prepare(body=body, **values)))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_workload_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install_plan_workload(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanWorkloadReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanWorkloadType]:
        return success(workload_type(workloads.read(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_workload_recover_preparation",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def recover_nvke_operator_install_plan_workload_preparation(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanWorkloadReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanWorkloadType]:
        return success(workload_type(workloads.recover_preparation(**asdict(input))))
