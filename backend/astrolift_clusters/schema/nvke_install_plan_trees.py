"""Reviewed execution-tree preparation/recovery with no native execution admission."""

from dataclasses import asdict

import strawberry
from strawberry.types import Info

from astrolift_clusters import nvke_install_plan_trees as trees
from astrolift_clusters.schema.nvke import nvke_errors
from astrolift_clusters.schema.nvke_install_plan_charts import NvkeOperatorInstallPlanChartsSourceInput
from astrolift_clusters.schema.nvke_install_plans import _request_text
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.input
class NvkeOperatorInstallPlanTreeSourceInput(NvkeOperatorInstallPlanChartsSourceInput):
    chart_artifact_id: GUID
    expected_chart_artifact_version: int
    chart_artifact_digest: str


@strawberry.input
class PrepareNvkeOperatorInstallPlanTreeInput(NvkeOperatorInstallPlanTreeSourceInput):
    request_text: str


@strawberry.input
class NvkeOperatorInstallPlanTreeReviewInput(NvkeOperatorInstallPlanTreeSourceInput):
    artifact_id: GUID


@strawberry.type
class NvkeOperatorInstallPlanTreeType:
    id: GUID
    version: int
    status: str
    plan_id: GUID
    plan_version: int
    operation_id: GUID
    operation_version: int
    site_id: GUID
    site_version: int
    authority_id: GUID
    authority_version: int
    plan_digest: str
    chart_artifact_id: GUID
    chart_artifact_version: int
    chart_artifact_digest: str
    tree_digest: str
    file_count: int
    bytes: str
    execution_state: str
    continuation_authorized: bool


def tree_type(data):
    value = dict(data)
    metadata = value.pop("metadata")
    return NvkeOperatorInstallPlanTreeType(
        **value,
        plan_digest=metadata["planDigest"],
        chart_artifact_digest=metadata["chartArtifactDigest"],
        tree_digest=metadata["treeDigest"],
        file_count=metadata["fileCount"],
        bytes=metadata["bytes"],
        execution_state=metadata["executionState"],
        continuation_authorized=metadata["continuationAuthorized"],
    )


@strawberry.type
class NvkeInstallPlanTreeMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_tree_prepare",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def prepare_nvke_operator_install_plan_tree(
        self,
        info: Info,
        input: PrepareNvkeOperatorInstallPlanTreeInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanTreeType]:
        values = asdict(input)
        body = _request_text(values.pop("request_text"))
        return success(tree_type(trees.prepare(body=body, **values)))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_tree_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install_plan_tree(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanTreeReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanTreeType]:
        return success(tree_type(trees.read(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_tree_recover_preparation",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def recover_nvke_operator_install_plan_tree_preparation(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanTreeReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanTreeType]:
        return success(tree_type(trees.recover_preparation(**asdict(input))))
