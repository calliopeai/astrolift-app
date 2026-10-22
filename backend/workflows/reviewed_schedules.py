"""Explicit schedule consent and fresh admission of its sealed reviewed plans.

Saving consent does not contact Temporal or claim an observed engine state.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from contextlib import contextmanager, nullcontext

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
)
from core.permissions import Permission, PermissionDenied, check_permission
from core.run_input_contract import canonical_bytes, digest
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest
from core.tenancy import TenantContext, get_current_tenant, tenant_context
from workflows.atomic_review import (
    CANONICALIZATION,
    MAX_PLAN_BYTES,
    WorkflowReviewError,
    authorize_agents,
    authorize_definitions,
    current_review_authority,
    materialize_review,
)
from workflows.models import (
    ReviewedWorkflowScheduleApproval,
    ReviewedWorkflowScheduleExecution,
    Workflow,
    WorkflowDefinition,
)
from workflows.reviewed_starts import _definition_graph
from workflows.scopes import reviewed_definition_scope


class ScheduleConsentDenied(WorkflowReviewError):
    def __init__(self, result):
        super().__init__("Current session verification is required")
        self.result = result


@contextmanager
def activation_admission(info):
    """Reuse native step-up and persisted session checks at consent admission."""
    from astrolift_identity.step_up import check_elevation
    from core.current_session import fresh_authenticated_session

    tenant = get_current_tenant()
    request = getattr(info.context, "request", None)
    original_session = getattr(request, "session", None)
    token = get_current_api_token()
    if token is not None and getattr(getattr(request, "_api_token", None), "pk", None) != token.pk:
        raise PermissionDenied(Permission.WORKFLOW_UPDATE, None, "The authenticated credential changed")
    try:
        if token is None and request is not None and hasattr(request, "session"):
            request.session = fresh_authenticated_session(
                request,
                actor_user_id=tenant.actor_user_id,
                permission=Permission.WORKFLOW_UPDATE,
            )
        before = abac.attributes_for(tenant.actor_user_id)
        # Person facts are read anew from the actual persisted browser session.
        attrs = abac.RequestAttributes(
            actor_user_id=tenant.actor_user_id,
            request=request,
            client_ip=before.client_ip,
            now=before.now,
            environment=before.environment,
            region=before.region,
            approvals=before.approvals,
            approval_request=before.approval_request,
        )
        with abac.request_attributes(attrs):
            deny = check_elevation(
                info,
                action_label="Approve a reviewed workflow schedule",
                resolver_name="Mutation.activate_reviewed_workflow_schedule",
            )
            if deny is not None:
                raise ScheduleConsentDenied(deny)
            yield
    finally:
        if token is None and request is not None and hasattr(request, "session"):
            request.session = original_session


def _credential_ceiling(token):
    if token is None:
        return {"kind": "person_approval"}
    return {
        "kind": "api_token",
        "tokenHash": token.token_hash,
        "userId": token.user_id,
        "organizationId": token.organization_id,
        "teamId": token.team_id,
        "scopes": sorted(token.scopes),
        "expiresAt": token.expires_at.isoformat() if token.expires_at else None,
    }


def approved_payload(approval):
    try:
        raw = decrypt(EncryptedSecret(approval.payload_backend_kind, bytes(approval.payload_ciphertext)))
        if len(raw) > MAX_PLAN_BYTES:
            raise ValueError("oversized payload")
        payload = json.loads(raw)
        if (
            approval.deleted_at is not None
            or approval.canonicalization != CANONICALIZATION
            or payload["canonicalization"] != CANONICALIZATION
            or digest(payload["plans"]) != approval.compiled_plans_digest
            or digest(payload["configuration"]) != approval.configuration_digest
            or hashlib.sha256(payload["canonicalReview"].encode()).hexdigest() != approval.review_digest
            or json.loads(payload["canonicalReview"])["revision"] != approval.definition_revision
            or payload["configuration"]["workflowGuid"] != str(approval.workflow.guid)
            or payload["configuration"]["definitionGuid"] != str(approval.definition.guid)
            or {**payload["reviewedConfiguration"], "isEnabled": True} != payload["configuration"]
            or digest(
                {
                    "reviewDigest": approval.review_digest,
                    "configurationDigest": digest(payload["reviewedConfiguration"]),
                    "compiledPlansDigest": approval.compiled_plans_digest,
                    "activationIntent": "ENABLE_REVIEWED_SCHEDULE",
                }
            )
            != payload["scheduleReviewDigest"]
        ):
            raise ValueError("receipt mismatch")
        return payload
    except Exception as exc:
        raise WorkflowReviewError("The approved schedule plan is unavailable; review is required") from exc


def _locked_review(workflow_id, organization_id):
    workflow = (
        Workflow.objects.select_for_update(of=("self",))
        .select_related("organization")
        .filter(guid=str(workflow_id), organization_id=organization_id)
        .first()
    )
    if workflow is None:
        raise WorkflowReviewError("Configured workflow not found")
    definition = (
        WorkflowDefinition.objects.select_for_update(of=("self",))
        .select_related("project__team", "organization")
        .filter(pk=workflow.definition_id)
        .first()
    )
    if definition is None or definition.organization_id not in (None, organization_id):
        raise WorkflowReviewError("Workflow definition is unavailable")
    workflow.definition = definition
    graph = _definition_graph(definition, lock=True, using="default")
    return workflow, definition, graph


def _materialize_schedule(definition, graph, workflow):
    try:
        return materialize_review(definition, graph, workflow, using="default", captured_at=timezone.now())
    except (ValueError, RuntimeError, ValidationError) as exc:
        raise WorkflowReviewError("Schedule content cannot be compiled; capture a new review") from exc


def _authorize_schedule(review):
    tenant = get_current_tenant()
    check_permission(
        Permission.WORKFLOW_UPDATE,
        scope=reviewed_definition_scope(
            review.definition, tenant.organization_id, Permission.WORKFLOW_UPDATE
        ),
    )
    authorize_definitions(review.graph, Permission.WORKFLOW_TRIGGER)
    selected_ids = {
        stage["agent_definition_id"]
        for plan in review.plans.values()
        for stage in plan["stages"]
        if stage["agent_definition_id"] is not None
    }
    authorize_agents({pk: review.agents[pk] for pk in selected_ids}, Permission.AGENT_DISPATCH)


def activate_reviewed_schedule(*, workflow_id, expected_digest, request_id, confirmed, admission=None):
    if confirmed is not True:
        raise WorkflowReviewError("Explicit person confirmation is required")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise WorkflowReviewError("requestId must contain 1 to 128 characters")
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise WorkflowReviewError("expectedScheduleReviewDigest must be the complete review digest")
    requested = {"workflowGuid": str(workflow_id), "scheduleReviewDigest": expected_digest, "confirmed": True}
    request_digest = hmac.new(
        settings.SECRET_KEY.encode(), canonical_bytes(requested), hashlib.sha256
    ).hexdigest()
    with transaction.atomic():
        tenant = get_current_tenant()
        with admission() if admission else nullcontext():
            with current_review_authority(Permission.WORKFLOW_UPDATE) as actor:
                organization_id, actor_id = tenant.organization_id, actor.pk
        identity = canonical_bytes([organization_id, actor_id, request_id])
        lock_key = int.from_bytes(
            hmac.new(settings.SECRET_KEY.encode(), identity, hashlib.sha256).digest()[:8], "big", signed=True
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])
        workflow, definition, graph = _locked_review(workflow_id, organization_id)
        # A caller may have waited past session elevation, credential or grant validity.
        with (
            admission() if admission else nullcontext(),
            current_review_authority(Permission.WORKFLOW_UPDATE),
        ):
            review = _materialize_schedule(definition, graph, workflow)
            _authorize_schedule(review)
            previous = (
                ReviewedWorkflowScheduleApproval.all_objects.filter(
                    organization_id=organization_id,
                    actor_key=f"user:{actor_id}",
                    request_id=request_id,
                )
                .select_related("workflow", "definition")
                .first()
            )
            if previous is not None:
                if previous.request_digest != request_digest or previous.deleted_at is not None:
                    raise WorkflowReviewError("requestId has already been used for different consent")
                return previous
            if review.schedule_review_digest != expected_digest:
                raise WorkflowReviewError(
                    "The reviewed schedule content changed; capture and confirm a new review"
                )
            if not definition.is_enabled:
                raise WorkflowReviewError(
                    "Enable the reviewed definition separately before schedule activation"
                )
            if workflow.trigger_kind != "schedule" or not workflow.schedule_cron:
                raise WorkflowReviewError("A schedule trigger and cron expression are required")
            from astrolift_registry.cron import CronValidationError, validate_cron_expression

            try:
                validate_cron_expression(workflow.schedule_cron)
            except CronValidationError as exc:
                raise WorkflowReviewError("The cron expression is invalid") from exc
            configuration = {**review.configuration, "isEnabled": True}
            payload = {
                "canonicalization": CANONICALIZATION,
                "canonicalReview": review.canonical_review,
                "reviewedConfiguration": review.configuration,
                "scheduleReviewDigest": review.schedule_review_digest,
                "configuration": configuration,
                "plans": review.plans,
            }
            encoded = canonical_bytes(payload)
            if len(encoded) > MAX_PLAN_BYTES:
                raise WorkflowReviewError("The approved schedule exceeds its supported byte limit")
            sealed = encrypt_at_rest(encoded)
            token = get_current_api_token()
            approval = ReviewedWorkflowScheduleApproval.objects.create(
                organization_id=organization_id,
                workflow=workflow,
                definition=definition,
                approver_id=actor_id,
                api_token=token,
                actor_key=f"user:{actor_id}",
                request_id=request_id,
                request_digest=request_digest,
                review_digest=review.review_digest,
                definition_revision=review.revision,
                configuration_digest=digest(configuration),
                compiled_plans_digest=review.compiled_plans_digest,
                canonicalization=CANONICALIZATION,
                credential_ceiling=_credential_ceiling(token),
                payload_backend_kind=sealed.backend_kind,
                payload_ciphertext=sealed.backend_ref,
            )
            workflow.is_enabled = True
            workflow.reviewed_schedule_approval = approval
            workflow.save(
                update_fields=["is_enabled", "reviewed_schedule_approval", "updated_at", "version"],
                skip_binding_validation=True,
            )
            return approval


@contextmanager
def approval_authority(approval):
    """Check recorded delegation, without fabricating a human session or request facts."""
    from astrolift_identity.models import ApiToken

    token = None
    if approval.api_token_id is not None:
        token = ApiToken.objects.filter(pk=approval.api_token_id).first()
        if token is None or _credential_ceiling(token) != approval.credential_ceiling:
            raise WorkflowReviewError("The approving credential changed; a new review is required")
    elif approval.credential_ceiling != {"kind": "person_approval"}:
        raise WorkflowReviewError("The approving authority is unavailable")
    state = set_current_api_token(token)
    try:
        with (
            tenant_context(
                TenantContext(
                    organization_id=approval.organization_id,
                    actor_user_id=approval.approver_id,
                    team_id=token.team_id if token else None,
                )
            ),
            abac.request_attributes(
                abac.RequestAttributes(actor_user_id=approval.approver_id, now=timezone.now())
            ),
            current_review_authority(Permission.WORKFLOW_UPDATE),
        ):
            yield
    finally:
        reset_current_api_token(state)


def admit_scheduled_run(workflow):
    """Called inside the timer's transaction before reserving a system run."""
    approval = (
        ReviewedWorkflowScheduleApproval.objects.select_related("workflow", "definition")
        .filter(
            pk=workflow.reviewed_schedule_approval_id,
            workflow_id=workflow.pk,
            organization_id=workflow.organization_id,
        )
        .first()
    )
    if approval is None or approval.definition_id != workflow.definition_id:
        raise WorkflowReviewError("The schedule approval is unavailable; review is required")
    payload = approved_payload(approval)
    with approval_authority(approval):
        locked, definition, graph = _locked_review(workflow.guid, workflow.organization_id)
        # Content locks can wait past revocation or credential expiry. Reload the
        # exact saved delegation and all authority after the last content lock.
        with approval_authority(approval):
            if locked.reviewed_schedule_approval_id != approval.pk:
                raise WorkflowReviewError("The current schedule approval changed; review is required")
            review = _materialize_schedule(definition, graph, locked)
            _authorize_schedule(review)
            if (
                review.review_digest != approval.review_digest
                or review.configuration_digest != approval.configuration_digest
                or review.compiled_plans_digest != approval.compiled_plans_digest
            ):
                raise WorkflowReviewError("The approved schedule content changed; review is required")
    return approval, payload


def link_scheduled_run(run, approval):
    return ReviewedWorkflowScheduleExecution.objects.create(
        workflow_run=run,
        approval=approval,
        organization_id=run.organization_id,
    )
