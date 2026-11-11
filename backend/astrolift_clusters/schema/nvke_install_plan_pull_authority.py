"""Audited current-admin controls for retained pull-authority observations."""

from dataclasses import asdict

import strawberry
from strawberry.types import Info

from astrolift_clusters import nvke_install_plan_pull_authority as artifacts
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
class PrepareNvkeOperatorInstallPlanPullAuthorityInput(NvkeOperatorInstallPlanImmutableSourceInput):
    request_text: str


@strawberry.input
class NvkeOperatorInstallPlanPullAuthorityReviewInput(NvkeOperatorInstallPlanImmutableReviewInput):
    pass


@strawberry.type
class NvkeOperatorInstallPlanPullAuthorityType:
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
    authority_digest: str
    pod_count: int
    service_account_count: int
    secret_count: int
    missing_reference_count: int


def artifact_type(data):
    value = dict(data)
    metadata = value.pop("metadata")
    fields = {
        "artifact_digest": "artifactDigest",
        "source_digest": "sourceDigest",
        "capture_digest": "captureDigest",
        "authority_digest": "authorityDigest",
        "observed_at": "observedAt",
        "pod_count": "podCount",
        "service_account_count": "serviceAccountCount",
        "secret_count": "secretCount",
        "missing_reference_count": "missingReferenceCount",
        "execution_state": "executionState",
        "continuation_authorized": "continuationAuthorized",
    }
    return NvkeOperatorInstallPlanPullAuthorityType(
        **value, **{field: metadata[key] for field, key in fields.items()}
    )


@strawberry.type
class NvkeInstallPlanPullAuthorityMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_pull_authority_prepare",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def prepare_nvke_operator_install_plan_pull_authority(
        self, info: Info, input: PrepareNvkeOperatorInstallPlanPullAuthorityInput
    ) -> MutationResultType[NvkeOperatorInstallPlanPullAuthorityType]:
        values = asdict(input)
        body = _request_text(values.pop("request_text"))
        result = artifacts.prepare(body=body, **values)
        return success(artifact_type(result))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_pull_authority_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install_plan_pull_authority(
        self, info: Info, input: NvkeOperatorInstallPlanPullAuthorityReviewInput
    ) -> MutationResultType[NvkeOperatorInstallPlanPullAuthorityType]:
        values = asdict(input)
        result = artifacts.read(**values)
        return success(artifact_type(result))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_pull_authority_recover_preparation",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def recover_nvke_operator_install_plan_pull_authority_preparation(
        self, info: Info, input: NvkeOperatorInstallPlanPullAuthorityReviewInput
    ) -> MutationResultType[NvkeOperatorInstallPlanPullAuthorityType]:
        values = asdict(input)
        result = artifacts.recover_preparation(**values)
        return success(artifact_type(result))
