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
