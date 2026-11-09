"""Typed, audited preparation and source-bound inspection of retained install plans."""

import json
import re
from dataclasses import asdict
from datetime import datetime

import strawberry
from nvke.client import NvkeError
from nvke.operator_contract import MAX_REQUEST_BYTES
from strawberry.types import Info

from astrolift_clusters import nvke_install_plans as plans
from astrolift_clusters.schema.nvke import nvke_errors
from astrolift_clusters.schema.nvke_operator import (
    NvkeOperatorInstallWorkloadType,
    NvkeOperatorJobReviewInput,
)
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID, MutationResultType, success
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission


@strawberry.input
class PrepareNvkeOperatorInstallPlanInput(NvkeOperatorJobReviewInput):
    request_text: str


@strawberry.input
class NvkeOperatorInstallPlanReviewInput(NvkeOperatorJobReviewInput):
    plan_id: GUID


@strawberry.type
class NvkeOperatorInstallPlanSourceType:
    site_id: GUID
    instance_id: GUID
    job_id: GUID
    native_run_id: GUID
    native_revision: str
    native_exit_code: int
    bundle_manifest_sha256: str
    native_arguments_sha256: str
    retained_inputs_sha256: str
    proposed_inputs_sha256: str
    proposed_execution_sha256: str
    private_source_sha256: str
    review_digest: str
    stderr_sha256: str
    original_started_at: datetime
    review_observed_at: datetime
    original_deployment_evidence: str


@strawberry.type
class NvkeOperatorInstallPlanHeaderType:
    format: int
    scope: str
    id: GUID
    prepared_at: datetime
    source: NvkeOperatorInstallPlanSourceType
    phase_evidence: str
    requires_original_source_verification: bool
    requires_explicit_acknowledgement: bool
    continuation_authorized: bool
    first_remaining_ordinal: int
    plan_digest: str


@strawberry.type
class NvkeOperatorInstallPlanInfrastructureType:
    api_ready: bool
    observed_at: datetime
    cluster_uid: GUID
    workload_count: int


@strawberry.type
class NvkeOperatorInstallPlanType:
    id: GUID
    version: int
    status: str
    operation_id: GUID
    operation_version: int
    site_id: GUID
    site_version: int
    authority_id: GUID
    authority_version: int
    item_count: int
    retained_bytes: str
    proposed_bytes: str
    plan: NvkeOperatorInstallPlanHeaderType
    infrastructure: NvkeOperatorInstallPlanInfrastructureType


@strawberry.type
class NvkeOperatorInstallPlanItemType:
    name: str
    type: str
    original_type: str
    content_sha256: str
    original_content_sha256: str
    ordinal: int
    original_observation: str
    proposed_action: str


@strawberry.type
class NvkeOperatorInstallPlanItemPageType:
    plan_id: GUID
    plan_digest: str
    view: str
    items: list[NvkeOperatorInstallPlanItemType]
    next_cursor: str | None
    total_count: int


@strawberry.type
class NvkeOperatorInstallPlanWorkloadPageType:
    plan_id: GUID
    plan_digest: str
    view: str
    items: list[NvkeOperatorInstallWorkloadType]
    next_cursor: str | None
    total_count: int


def _snake(data):
    return {re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower(): value for key, value in data.items()}


def plan_type(data):
    data = dict(data)
    metadata = data.pop("metadata")
    header = _snake(metadata["plan"])
    source = _snake(header.pop("source"))
    for key in ("original_started_at", "review_observed_at"):
        source[key] = datetime.fromisoformat(source[key])
    header["source"] = NvkeOperatorInstallPlanSourceType(**source)
    header["prepared_at"] = datetime.fromisoformat(header["prepared_at"])
    infrastructure = _snake(metadata["infrastructure"])
    infrastructure["observed_at"] = datetime.fromisoformat(infrastructure["observed_at"])
    return NvkeOperatorInstallPlanType(
        **data,
        item_count=metadata["itemCount"],
        retained_bytes=metadata["retainedBytes"],
        proposed_bytes=metadata["proposedBytes"],
        plan=NvkeOperatorInstallPlanHeaderType(**header),
        infrastructure=NvkeOperatorInstallPlanInfrastructureType(**infrastructure),
    )


def _request_text(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError
            result[key] = item
        return result

    try:
        if not isinstance(value, str) or len(value.encode()) > MAX_REQUEST_BYTES:
            raise ValueError
        body = json.loads(value, object_pairs_hook=pairs)
        if not isinstance(body, dict):
            raise ValueError
        return body
    except (ValueError, TypeError, UnicodeError):
        raise NvkeError("INVALID_INPUT", "Review one bounded unambiguous install plan request") from None


@strawberry.type
class NvkeInstallPlanQuery:
    @strawberry.field
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def astrolift_nvke_operator_install_plan_item_page(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanReviewInput,
        plan_digest: str,
        after: str | None = None,
        limit: int = 25,
        search: str | None = None,
    ) -> MutationResultType[NvkeOperatorInstallPlanItemPageType]:
        data = plans.page(
            **asdict(input),
            plan_digest=plan_digest,
            view="items",
            after=after,
            limit=limit,
            search=search or "",
        )
        return success(
            NvkeOperatorInstallPlanItemPageType(
                **{
                    **data,
                    "items": [NvkeOperatorInstallPlanItemType(**_snake(item)) for item in data["items"]],
                }
            )
        )

    @strawberry.field
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def astrolift_nvke_operator_install_plan_workload_page(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanReviewInput,
        plan_digest: str,
        after: str | None = None,
        limit: int = 25,
        search: str | None = None,
    ) -> MutationResultType[NvkeOperatorInstallPlanWorkloadPageType]:
        data = plans.page(
            **asdict(input),
            plan_digest=plan_digest,
            view="workloads",
            after=after,
            limit=limit,
            search=search or "",
        )
        return success(
            NvkeOperatorInstallPlanWorkloadPageType(
                **{
                    **data,
                    "items": [NvkeOperatorInstallWorkloadType(**_snake(item)) for item in data["items"]],
                }
            )
        )


@strawberry.type
class NvkeInstallPlanMutation:
    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_prepare",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def prepare_nvke_operator_install_plan(
        self,
        info: Info,
        input: PrepareNvkeOperatorInstallPlanInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanType]:
        values = asdict(input)
        body = _request_text(values.pop("request_text"))
        return success(plan_type(plans.prepare(body=body, **values)))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_review",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def review_nvke_operator_install_plan(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanType]:
        return success(plan_type(plans.read(**asdict(input))))

    @strawberry.mutation
    @mutation_audit(
        action="nvke.operator_install_plan_recover_preparation",
        target=lambda root, info, input: ("nvke_operation", str(input.operation_id)),
    )
    @nvke_errors
    @require_permission(Permission.NVKE_ADMIN, scope=cluster_catalog_org_scope(Permission.NVKE_ADMIN))
    @tenant_scoped()
    def recover_nvke_operator_install_plan_preparation(
        self,
        info: Info,
        input: NvkeOperatorInstallPlanReviewInput,
    ) -> MutationResultType[NvkeOperatorInstallPlanType]:
        return success(plan_type(plans.recover_preparation(**asdict(input))))
