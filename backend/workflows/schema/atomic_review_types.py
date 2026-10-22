"""Complete native review data, kept separate from execution and engine receipts."""

from __future__ import annotations

import datetime as dt
import enum

import strawberry

from astrolift_graphql import GUID


@strawberry.enum
class WorkflowReviewConsistency(enum.Enum):
    POSTGRES_REPEATABLE_READ = "postgres_repeatable_read"


@strawberry.type
class WorkflowDefinitionFullReview:
    definition_id: GUID
    revision: str
    captured_at: dt.datetime
    consistency: WorkflowReviewConsistency
    canonicalization: str
    review_digest: str
    canonical_review: str
    root_manifest: str
    input_schema_digest: str


@strawberry.type
class WorkflowScheduleFullReview(WorkflowDefinitionFullReview):
    workflow_id: GUID
    canonical_configuration: str
    configuration_digest: str
    compiled_plans_digest: str
    schedule_review_digest: str
    activation_intent: str


def definition_review_fields(capture):
    from workflows.atomic_review import CANONICALIZATION

    return {
        "definition_id": GUID(str(capture.definition.guid)),
        "revision": capture.revision,
        "captured_at": capture.captured_at,
        "consistency": WorkflowReviewConsistency.POSTGRES_REPEATABLE_READ,
        "canonicalization": CANONICALIZATION,
        "review_digest": capture.review_digest,
        "canonical_review": capture.canonical_review,
        "root_manifest": capture.root_manifest,
        "input_schema_digest": capture.input_schema_digest,
    }


def definition_review_to_type(capture):
    return WorkflowDefinitionFullReview(**definition_review_fields(capture))


def schedule_review_to_type(capture):
    from core.run_input_contract import canonical_bytes

    return WorkflowScheduleFullReview(
        **definition_review_fields(capture),
        workflow_id=GUID(str(capture.workflow.guid)),
        canonical_configuration=canonical_bytes(capture.configuration).decode(),
        configuration_digest=capture.configuration_digest,
        compiled_plans_digest=capture.compiled_plans_digest,
        schedule_review_digest=capture.schedule_review_digest,
        activation_intent="ENABLE_REVIEWED_SCHEDULE",
    )


@strawberry.input
class ActivateReviewedWorkflowScheduleInput:
    workflow_id: GUID
    expected_schedule_review_digest: str
    request_id: str
    confirmed: bool = False


@strawberry.type
class ReviewedWorkflowScheduleApprovalReceipt:
    id: GUID
    workflow_id: GUID
    definition_id: GUID
    approved_by_user_id: strawberry.ID
    approved_at: dt.datetime
    review_digest: str
    definition_revision: str
    configuration_digest: str
    compiled_plans_digest: str
    credential_kind: str
    credential_expires_at: dt.datetime | None
    is_current: bool
    schedule_review_digest: str
    database_status: str
    engine_state: str


def approval_to_type(approval):
    from workflows.models import Workflow
    from workflows.reviewed_schedules import approved_payload

    expires = approval.credential_ceiling.get("expiresAt")
    return ReviewedWorkflowScheduleApprovalReceipt(
        id=GUID(str(approval.guid)),
        workflow_id=GUID(str(approval.workflow.guid)),
        definition_id=GUID(str(approval.definition.guid)),
        approved_by_user_id=strawberry.ID(str(approval.approver_id)),
        approved_at=approval.created_at,
        review_digest=approval.review_digest,
        definition_revision=approval.definition_revision,
        configuration_digest=approval.configuration_digest,
        compiled_plans_digest=approval.compiled_plans_digest,
        credential_kind=approval.credential_ceiling["kind"],
        credential_expires_at=dt.datetime.fromisoformat(expires) if expires else None,
        is_current=Workflow.objects.filter(
            pk=approval.workflow_id, reviewed_schedule_approval_id=approval.pk
        ).exists(),
        schedule_review_digest=approved_payload(approval)["scheduleReviewDigest"],
        database_status="SAVED",
        engine_state="UNCONFIRMED",
    )
