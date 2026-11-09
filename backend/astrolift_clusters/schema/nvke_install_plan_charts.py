"""Reviewed chart preparation and recovery; no native execution admission."""

from dataclasses import asdict

import strawberry
from strawberry.types import Info

from astrolift_clusters import nvke_install_plan_charts as charts
from astrolift_clusters.schema.nvke import nvke_errors
from astrolift_clusters.schema.nvke_install_plans import NvkeOperatorInstallPlanReviewInput, _request_text
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.input
class NvkeOperatorInstallPlanChartsSourceInput(NvkeOperatorInstallPlanReviewInput):
    expected_plan_version: int
    plan_digest: str


@strawberry.input
class PrepareNvkeOperatorInstallPlanChartsInput(NvkeOperatorInstallPlanChartsSourceInput):
    request_text: str


@strawberry.input
class NvkeOperatorInstallPlanChartsReviewInput(NvkeOperatorInstallPlanChartsSourceInput):
    artifact_id: GUID


@strawberry.type
class NvkeOperatorInstallPlanChartsType:
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
    artifact_digest: str
    chart_count: int
    bytes: str
    expanded_bytes: str
    execution_state: str
    continuation_authorized: bool


def chart_type(data):
    value = dict(data)
    metadata = value.pop("metadata")
    return NvkeOperatorInstallPlanChartsType(
        **value,
        plan_digest=metadata["planDigest"],
        artifact_digest=metadata["artifactDigest"],
        chart_count=metadata["chartCount"],
        bytes=metadata["bytes"],
        expanded_bytes=metadata["expandedBytes"],
        execution_state=metadata["executionState"],
        continuation_authorized=metadata["continuationAuthorized"],
    )


@strawberry.type
class NvkeInstallPlanChartsMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_charts_prepare",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def prepare_nvke_operator_install_plan_charts(
        self,
        info: Info,
        input: PrepareNvkeOperatorInstallPlanChartsInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanChartsType]:
        values = asdict(input)
        body = _request_text(values.pop("request_text"))
        return success(chart_type(charts.prepare(body=body, **values)))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_charts_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install_plan_charts(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanChartsReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanChartsType]:
        return success(chart_type(charts.read(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_charts_recover_preparation",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def recover_nvke_operator_install_plan_charts_preparation(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanChartsReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanChartsType]:
        return success(chart_type(charts.recover_preparation(**asdict(input))))
