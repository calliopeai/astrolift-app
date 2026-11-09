"""Reviewed render-verification preparation/recovery with no native execution admission."""

from dataclasses import asdict

import strawberry
from strawberry.types import Info

from astrolift_clusters import nvke_install_plan_verifications as verifications
from astrolift_clusters.schema.nvke import nvke_errors
from astrolift_clusters.schema.nvke_install_plan_contexts import NvkeOperatorInstallPlanContextSourceInput
from astrolift_clusters.schema.nvke_install_plans import _request_text
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.input
class NvkeOperatorInstallPlanVerificationSourceInput(NvkeOperatorInstallPlanContextSourceInput):
    context_id: GUID
    expected_context_version: int
    context_digest: str


@strawberry.input
class PrepareNvkeOperatorInstallPlanVerificationInput(NvkeOperatorInstallPlanVerificationSourceInput):
    request_text: str


@strawberry.input
class NvkeOperatorInstallPlanVerificationReviewInput(NvkeOperatorInstallPlanVerificationSourceInput):
    artifact_id: GUID


@strawberry.type
class NvkeOperatorInstallPlanVerificationType:
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
    context_id: GUID
    context_version: int
    artifact_digest: str
    verification_digest: str
    requests: int
    bytes: str
    observed_at: str
    execution_state: str
    continuation_authorized: bool


def verification_type(data):
    value = dict(data)
    metadata = value.pop("metadata")
    return NvkeOperatorInstallPlanVerificationType(
        **value,
        plan_digest=metadata["planDigest"],
        tree_digest=metadata["treeDigest"],
        item=metadata["item"],
        ordinal=metadata["ordinal"],
        capture_digest=metadata["captureDigest"],
        frozen_chart_sha256=metadata["frozenChartSha256"],
        context_digest=metadata["contextDigest"],
        artifact_digest=metadata["artifactDigest"],
        verification_digest=metadata["verificationDigest"],
        requests=metadata["requests"],
        bytes=metadata["bytes"],
        observed_at=metadata["observedAt"],
        execution_state=metadata["executionState"],
        continuation_authorized=metadata["continuationAuthorized"],
    )


@strawberry.type
class NvkeInstallPlanVerificationMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_verification_prepare",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def prepare_nvke_operator_install_plan_verification(
        self,
        info: Info,
        input: PrepareNvkeOperatorInstallPlanVerificationInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanVerificationType]:
        values = asdict(input)
        body = _request_text(values.pop("request_text"))
        return success(verification_type(verifications.prepare(body=body, **values)))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_verification_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install_plan_verification(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanVerificationReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanVerificationType]:
        return success(verification_type(verifications.read(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_verification_recover_preparation",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def recover_nvke_operator_install_plan_verification_preparation(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanVerificationReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanVerificationType]:
        return success(verification_type(verifications.recover_preparation(**asdict(input))))
