"""Reviewed operator authority and private native tooling receipts."""

from dataclasses import asdict
from datetime import datetime

import strawberry
from nvke.operator_contract import CATALOG, REVISION
from strawberry.types import Info

from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_operator_authority as authority
from astrolift_clusters import nvke_operator_jobs as jobs
from astrolift_clusters.schema.nvke import NvkeOperationType, nvke_errors, operation_type
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.type
class NvkeOperatorAuthorityType:
    id: GUID
    version: int
    site_id: GUID
    site_version: int
    endpoint: str
    instance_id: GUID
    bundle_manifest_sha256: str
    is_enabled: bool
    timeout_seconds: int
    has_credentials: bool
    verified_at: datetime | None
    verification_code: str
    deleted_at: datetime | None


def authority_type(row):
    return NvkeOperatorAuthorityType(
        id=GUID(str(row.guid)),
        version=row.version,
        site_id=GUID(str(row.site.guid)),
        site_version=row.site.version,
        endpoint=row.endpoint,
        instance_id=GUID(str(row.instance_id)),
        bundle_manifest_sha256=row.bundle_manifest_sha256,
        is_enabled=row.is_enabled,
        timeout_seconds=row.timeout_seconds,
        has_credentials=bool(row.credential_ciphertext),
        verified_at=row.verified_at,
        verification_code=row.verification_code,
        deleted_at=row.deleted_at,
    )


@strawberry.input
class NvkeOperatorCredentialsInput:
    ca_pem: str = ""
    client_certificate_pem: str = ""
    client_key_pem: str = ""


@strawberry.input
class NvkeOperatorAuthorityReviewInput:
    site_id: GUID
    expected_actor_id: int
    expected_site_version: int
    expected_version: int


@strawberry.input
class ConfigureNvkeOperatorAuthorityInput(NvkeOperatorAuthorityReviewInput):
    endpoint: str
    instance_id: GUID
    bundle_manifest_sha256: str
    timeout_seconds: int = 30
    credentials: NvkeOperatorCredentialsInput | None = None


@strawberry.input
class SetNvkeOperatorAuthorityEnabledInput(NvkeOperatorAuthorityReviewInput):
    enabled: bool


@strawberry.input
class RequestNvkeOperatorJobInput:
    site_id: GUID
    expected_actor_id: int
    expected_site_version: int
    expected_authority_version: int
    request_id: str
    request_text: str
    acknowledge_infrastructure_change: bool = False
    timeout_seconds: int = 7200


@strawberry.input
class NvkeOperatorJobReviewInput:
    operation_id: GUID
    expected_actor_id: int
    expected_site_version: int
    expected_authority_version: int
    expected_operation_version: int


@strawberry.input
class ReadNvkeOperatorArtifactInput(NvkeOperatorJobReviewInput):
    name: str
    offset: str = "0"
    limit: int = 262144


@strawberry.type
class NvkeOperatorOptionType:
    name: str
    kind: str
    required: bool
    choices: list[str]


@strawberry.type
class NvkeOperatorCommandType:
    key: str
    positionals: list[str]
    options: list[NvkeOperatorOptionType]
    infrastructure_change: bool


@strawberry.type
class NvkeOperatorContractType:
    revision: str
    commands: list[NvkeOperatorCommandType]


@strawberry.type
class NvkeOperatorArtifactType:
    name: str
    size: str
    sha256: str


@strawberry.type
class NvkeOperatorJobPageType:
    site_id: GUID
    site_version: int
    operation_id: GUID
    operation_version: int
    authority_id: GUID
    authority_version: int
    instance_id: GUID
    bundle_manifest_sha256: str
    native_revision: str
    command: str
    found: bool
    state: str
    effectful: bool
    cancellation_requested: bool
    native_exit_code: int | None
    error_code: str | None
    reconciled_from_native_receipt: bool
    receipt_sha256: str
    items: list[NvkeOperatorArtifactType]
    next_cursor: str | None
    total_count: int


@strawberry.type
class NvkeOperatorInstallEvidenceType:
    guard_sha256: str
    started_sha256: str
    finished_sha256: str
    stdout_sha256: str
    stderr_sha256: str


@strawberry.type
class NvkeOperatorInstallWorkloadType:
    namespace: str
    kind: str
    name: str
    generation: str
    observed_generation: str | None
    desired_replicas: str
    ready_replicas: str | None
    updated_replicas: str | None
    observation: str


@strawberry.type
class NvkeOperatorInstallReviewType:
    snapshot_id: GUID
    snapshot_expires_at: datetime
    workload_count: int
    site_id: GUID
    site_version: int
    operation_id: GUID
    operation_version: int
    authority_id: GUID
    authority_version: int
    instance_id: GUID
    bundle_manifest_sha256: str
    native_revision: str
    native_run_id: GUID
    review_digest: str
    observed_at: datetime
    original_started_at: datetime
    native_arguments_sha256: str
    native_exit_code: int
    api_ready: bool
    continuation_authorized: bool
    reviewer_sha256: str
    qualification_helper_sha256: str
    evidence: NvkeOperatorInstallEvidenceType
    workloads: list[NvkeOperatorInstallWorkloadType]


@strawberry.type
class NvkeOperatorInstallWorkloadPageType:
    snapshot_id: GUID
    operation_id: GUID
    operation_version: int
    review_digest: str
    items: list[NvkeOperatorInstallWorkloadType]
    next_cursor: str | None
    total_count: int


def contract_type():
    return NvkeOperatorContractType(
        revision=REVISION,
        commands=[
            NvkeOperatorCommandType(
                key=key,
                positionals=value["positionals"],
                infrastructure_change=key
                in {"bootstrap.site", "management.install", "management.configure-access"},
                options=[
                    NvkeOperatorOptionType(
                        name=name,
                        kind=kind["type"] if isinstance(kind, dict) else kind,
                        choices=kind.get("choices", []) if isinstance(kind, dict) else [],
                        required=name in value["required"],
                    )
                    for name, kind in value["options"].items()
                ],
            )
            for key, value in CATALOG.items()
        ],
    )


@strawberry.type
class NvkeOperatorQuery:
    @strawberry.field
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def astrolift_nvke_operator_install_workload_page(
        self,
        info: Info,
        input: NvkeOperatorJobReviewInput,
        snapshot_id: GUID,
        after: str | None = None,
        limit: int = 25,
        search: str | None = None,
    ) -> MutationResultType[NvkeOperatorInstallWorkloadPageType]:
        data = jobs.install_workload_page(
            snapshot_id=snapshot_id, after=after, limit=limit, search=search or "", **asdict(input)
        )
        return success(
            NvkeOperatorInstallWorkloadPageType(
                **{**data, "items": [NvkeOperatorInstallWorkloadType(**item) for item in data["items"]]}
            )
        )

    @strawberry.field
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def astrolift_nvke_operator_job_page(
        self,
        info: Info,
        input: NvkeOperatorJobReviewInput,
        after: str | None = None,
        limit: int = 50,
        search: str | None = None,
    ) -> MutationResultType[NvkeOperatorJobPageType]:
        data = jobs.artifact_page(after=after, limit=limit, search=search or "", **asdict(input))
        return success(
            NvkeOperatorJobPageType(
                **{
                    **data,
                    "items": [NvkeOperatorArtifactType(**item) for item in data["items"]],
                }
            )
        )

    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_operator_authority(
        self, info: Info, site_id: GUID
    ) -> NvkeOperatorAuthorityType | None:
        row = authority.read(str(site_id))
        return authority_type(row) if row else None

    @strawberry.field
    @require_permission(Permission.NVKE_READ, scope=cluster_catalog_org_scope(Permission.NVKE_READ))
    @tenant_scoped()
    def astrolift_nvke_operator_contract(self, info: Info) -> NvkeOperatorContractType:
        return contract_type()

    @strawberry.field
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def astrolift_nvke_operator_artifact_text(
        self, info: Info, input: ReadNvkeOperatorArtifactInput
    ) -> MutationResultType[str]:
        return success(outbox.canonical(jobs.artifact(**asdict(input))).decode("utf-8"))


@strawberry.type
class NvkeOperatorMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_configure", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def configure_nvke_operator_authority(
        self, info: Info, input: ConfigureNvkeOperatorAuthorityInput
    ) -> MutationResultType[NvkeOperatorAuthorityType]:
        return success(authority_type(authority.configure(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_verify", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def verify_nvke_operator_authority(
        self, info: Info, input: NvkeOperatorAuthorityReviewInput
    ) -> MutationResultType[NvkeOperatorAuthorityType]:
        return success(authority_type(authority.verify(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_enable", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def set_nvke_operator_authority_enabled(
        self, info: Info, input: SetNvkeOperatorAuthorityEnabledInput
    ) -> MutationResultType[NvkeOperatorAuthorityType]:
        return success(authority_type(authority.set_enabled(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_remove", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def remove_nvke_operator_authority(
        self, info: Info, input: NvkeOperatorAuthorityReviewInput
    ) -> MutationResultType[NvkeOperatorAuthorityType]:
        return success(authority_type(authority.set_enabled(**asdict(input), remove=True)))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_request", target=lambda root, info, input: ("nvke_site", str(input.site_id))
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def request_nvke_operator_job(
        self, info: Info, input: RequestNvkeOperatorJobInput
    ) -> MutationResultType[NvkeOperationType]:
        return success(operation_type(jobs.reserve(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_reconcile",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def reconcile_nvke_operator_job(
        self, info: Info, input: NvkeOperatorJobReviewInput
    ) -> MutationResultType[NvkeOperationType]:
        return success(operation_type(jobs.reconcile(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_stop",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def stop_nvke_operator_job(
        self, info: Info, input: NvkeOperatorJobReviewInput
    ) -> MutationResultType[NvkeOperationType]:
        return success(operation_type(jobs.stop(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install(
        self, info: Info, input: NvkeOperatorJobReviewInput
    ) -> MutationResultType[NvkeOperatorInstallReviewType]:
        data = jobs.install_review(**asdict(input))
        return success(
            NvkeOperatorInstallReviewType(
                **{
                    **data,
                    "evidence": NvkeOperatorInstallEvidenceType(**data["evidence"]),
                    "workloads": [NvkeOperatorInstallWorkloadType(**item) for item in data["workloads"]],
                }
            )
        )
