"""Reviewed render-context preparation/recovery with no native execution admission."""

from dataclasses import asdict

import strawberry
from strawberry.types import Info

from astrolift_clusters import nvke_install_plan_contexts as contexts
from astrolift_clusters.schema.nvke import nvke_errors
from astrolift_clusters.schema.nvke_install_plan_trees import NvkeOperatorInstallPlanTreeSourceInput
from astrolift_clusters.schema.nvke_install_plans import _request_text
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.input
class NvkeOperatorInstallPlanContextSourceInput(NvkeOperatorInstallPlanTreeSourceInput):
    tree_id: GUID
    expected_tree_version: int
    tree_digest: str
    item: str
    ordinal: int


@strawberry.input
class PrepareNvkeOperatorInstallPlanContextInput(NvkeOperatorInstallPlanContextSourceInput):
    request_text: str


@strawberry.input
class NvkeOperatorInstallPlanContextReviewInput(NvkeOperatorInstallPlanContextSourceInput):
    artifact_id: GUID


@strawberry.type
class NvkeOperatorInstallPlanContextType:
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
    tree_id: GUID
    tree_version: int
    item: str
    ordinal: int
    capture_digest: str
    frozen_chart_sha256: str
    context_digest: str
    document_count: int
    lookup_count: int
    observed_at: str
    execution_state: str
    continuation_authorized: bool


def context_type(data):
    value = dict(data)
    metadata = value.pop("metadata")
    return NvkeOperatorInstallPlanContextType(
        **value,
        plan_digest=metadata["planDigest"],
        tree_digest=metadata["treeDigest"],
        item=metadata["item"],
        ordinal=metadata["ordinal"],
        capture_digest=metadata["captureDigest"],
        frozen_chart_sha256=metadata["frozenChartSha256"],
        context_digest=metadata["contextDigest"],
        document_count=metadata["documentCount"],
        lookup_count=metadata["lookupCount"],
        observed_at=metadata["observedAt"],
        execution_state=metadata["executionState"],
        continuation_authorized=metadata["continuationAuthorized"],
    )


@strawberry.type
class NvkeInstallPlanContextMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_context_prepare",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def prepare_nvke_operator_install_plan_context(
        self,
        info: Info,
        input: PrepareNvkeOperatorInstallPlanContextInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanContextType]:
        values = asdict(input)
        body = _request_text(values.pop("request_text"))
        return success(context_type(contexts.prepare(body=body, **values)))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_context_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install_plan_context(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanContextReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanContextType]:
        return success(context_type(contexts.read(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_context_recover_preparation",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def recover_nvke_operator_install_plan_context_preparation(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanContextReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanContextType]:
        return success(context_type(contexts.recover_preparation(**asdict(input))))
