"""Audited admin controls for immutable payloads and sealed execution trees."""

from dataclasses import asdict

import strawberry
from strawberry.types import Info

from astrolift_clusters import nvke_install_plan_immutable as artifacts
from astrolift_clusters.schema.nvke import nvke_errors
from astrolift_clusters.schema.nvke_install_plan_image_bindings import (
    NvkeOperatorInstallPlanImageBindingSourceInput,
)
from astrolift_clusters.schema.nvke_install_plans import _request_text
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.input
class NvkeOperatorInstallPlanImmutableSourceInput(NvkeOperatorInstallPlanImageBindingSourceInput):
    binding_id: GUID
    expected_binding_version: int
    binding_digest: str


@strawberry.input
class PrepareNvkeOperatorInstallPlanImmutableInput(NvkeOperatorInstallPlanImmutableSourceInput):
    request_text: str


@strawberry.input
class NvkeOperatorInstallPlanImmutableReviewInput(NvkeOperatorInstallPlanImmutableSourceInput):
    artifact_id: GUID


@strawberry.type
class NvkeOperatorInstallPlanImmutableArtifactType:
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
    file_count: int
    observed_at: str
    source_observed_at: str
    execution_state: str
    continuation_authorized: bool


@strawberry.type
class NvkeOperatorInstallPlanImagePayloadType(NvkeOperatorInstallPlanImmutableArtifactType):
    payload_digest: str
    payload_bytes: str
    resource_count: int
    image_use_count: int
    rewrite_count: int


@strawberry.type
class NvkeOperatorInstallPlanImmutableExecutionType(NvkeOperatorInstallPlanImmutableArtifactType):
    execution_digest: str
    bytes: str
    phase_count: int
    skipped_phase_count: int
    import_count: int


def artifact_type(data, *, tree):
    value = dict(data)
    metadata = value.pop("metadata")
    fields = {
        "artifact_digest": "artifactDigest",
        "source_digest": "sourceDigest",
        "capture_digest": "captureDigest",
        "file_count": "fileCount",
        "observed_at": "observedAt",
        "source_observed_at": "sourceObservedAt",
        "execution_state": "executionState",
        "continuation_authorized": "continuationAuthorized",
    }
    if tree:
        fields.update(
            {
                "execution_digest": "executionDigest",
                "bytes": "bytes",
                "phase_count": "phaseCount",
                "skipped_phase_count": "skippedPhaseCount",
                "import_count": "importCount",
            }
        )
        kind = NvkeOperatorInstallPlanImmutableExecutionType
    else:
        fields.update(
            {
                "payload_digest": "payloadDigest",
                "payload_bytes": "payloadBytes",
                "resource_count": "resourceCount",
                "image_use_count": "imageUseCount",
                "rewrite_count": "rewriteCount",
            }
        )
        kind = NvkeOperatorInstallPlanImagePayloadType
    return kind(**value, **{field: metadata[key] for field, key in fields.items()})


@strawberry.type
class NvkeInstallPlanImmutableMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_image_payload_prepare",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def prepare_nvke_operator_install_plan_image_payload(
        self, info: Info, input: PrepareNvkeOperatorInstallPlanImmutableInput
    ) -> MutationResultType[NvkeOperatorInstallPlanImagePayloadType]:
        values = asdict(input)
        body = _request_text(values.pop("request_text"))
        result = artifacts.prepare(body=body, tree=False, **values)
        return success(artifact_type(result, tree=False))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_image_payload_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install_plan_image_payload(
        self, info: Info, input: NvkeOperatorInstallPlanImmutableReviewInput
    ) -> MutationResultType[NvkeOperatorInstallPlanImagePayloadType]:
        values = asdict(input)
        result = artifacts.read(tree=False, **values)
        return success(artifact_type(result, tree=False))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_image_payload_recover_preparation",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def recover_nvke_operator_install_plan_image_payload_preparation(
        self, info: Info, input: NvkeOperatorInstallPlanImmutableReviewInput
    ) -> MutationResultType[NvkeOperatorInstallPlanImagePayloadType]:
        values = asdict(input)
        result = artifacts.recover_preparation(tree=False, **values)
        return success(artifact_type(result, tree=False))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_immutable_execution_tree_prepare",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def prepare_nvke_operator_install_plan_immutable_execution_tree(
        self, info: Info, input: PrepareNvkeOperatorInstallPlanImmutableInput
    ) -> MutationResultType[NvkeOperatorInstallPlanImmutableExecutionType]:
        values = asdict(input)
        body = _request_text(values.pop("request_text"))
        result = artifacts.prepare(body=body, tree=True, **values)
        return success(artifact_type(result, tree=True))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_immutable_execution_tree_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install_plan_immutable_execution_tree(
        self, info: Info, input: NvkeOperatorInstallPlanImmutableReviewInput
    ) -> MutationResultType[NvkeOperatorInstallPlanImmutableExecutionType]:
        values = asdict(input)
        result = artifacts.read(tree=True, **values)
        return success(artifact_type(result, tree=True))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_immutable_execution_tree_recover_preparation",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def recover_nvke_operator_install_plan_immutable_execution_tree_preparation(
        self, info: Info, input: NvkeOperatorInstallPlanImmutableReviewInput
    ) -> MutationResultType[NvkeOperatorInstallPlanImmutableExecutionType]:
        values = asdict(input)
        result = artifacts.recover_preparation(tree=True, **values)
        return success(artifact_type(result, tree=True))
