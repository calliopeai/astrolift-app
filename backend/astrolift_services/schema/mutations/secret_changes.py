"""SecretChangeMutations — split from the monolithic mutations module."""

from __future__ import annotations

from datetime import timedelta

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug
from astrolift_services.models import (
    AppSecretBundleRef,
    SecretBundle,
    SecretChangeApproval,
    SecretChangeProposal,
)
from astrolift_services.schema.mutations.helpers import (
    _actor_user,
    _caller_org_id,
    _is_eligible_secret_approver,
    _proposal_target_from_input,
    _proposal_ttl_seconds,
    _self_approve_secrets_allowed,
    _validate_env_key,
)
from astrolift_services.schema.mutations.types import (
    ApproveSecretChangeInput,
    ProposeSecretChangeInput,
    RejectSecretChangeInput,
    WithdrawSecretChangeInput,
)
from astrolift_services.schema.types import (
    SecretChangeProposalType,
    secret_change_proposal_to_type,
)
from astrolift_services.scopes import secret_change_proposal_app_scope
from astrolift_services.secret_change_apply import apply_proposal
from astrolift_services.secret_change_diff import build_diff
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission


@strawberry.type
class SecretChangeMutations:
    # ---- Secret-change proposals (#488) ------------------------------

    @strawberry.field
    @mutation_audit(action="app.secret.proposal.create")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug"))
    @tenant_scoped()
    def propose_secret_change(
        self,
        info: Info,
        input: ProposeSecretChangeInput,
    ) -> MutationResultType[SecretChangeProposalType]:
        """Explicit proposal creation (#488).

        Works regardless of whether the app has
        ``requires_secret_approval`` on — teams can opt in to the
        review trail without flipping the gate.  When the gate IS on,
        the legacy mutations (``setAppSecret`` etc.) proxy to the same
        underlying logic; this is just the canonical surface.
        """
        valid_ops = {o.value for o in SecretChangeProposal.Op}
        if input.op not in valid_ops:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"op must be one of {sorted(valid_ops)}",
                field="op",
            )
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=_caller_org_id()).first()
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "app not found",
                field="appSlug",
            )

        # Per-op validation + payload assembly.  We refuse to mint a
        # proposal that we know would fail on apply (missing key,
        # missing bundle, …) so the queue stays clean.
        env_name = input.environment_name or ""
        if input.op == SecretChangeProposal.Op.SET.value:
            if not input.key:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "key is required for op=set",
                    field="key",
                )
            msg = _validate_env_key(input.key)
            if msg:
                return gql_failure(ErrorCode.VALIDATION.value, msg, field="key")
            if input.value is None:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "value is required for op=set",
                    field="value",
                )
            payload: dict = {"key": input.key, "value": input.value}
        elif input.op == SecretChangeProposal.Op.DELETE.value:
            if not input.key:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "key is required for op=delete",
                    field="key",
                )
            payload = {"key": input.key}
        elif input.op == SecretChangeProposal.Op.ATTACH_BUNDLE.value:
            if not input.bundle_slug:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "bundleSlug is required for op=attach_bundle",
                    field="bundleSlug",
                )
            if not env_name:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "environmentName is required for op=attach_bundle",
                    field="environmentName",
                )
            bundle = SecretBundle.objects.filter(
                slug=input.bundle_slug,
                organization_id=_caller_org_id(),
                deleted_at__isnull=True,
            ).first()
            if bundle is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"bundle {input.bundle_slug!r} not found",
                    field="bundleSlug",
                )
            if bundle.organization_id != app.organization_id:
                return gql_failure(
                    ErrorCode.PERMISSION_DENIED.value,
                    "bundle and app belong to different organizations",
                )
            payload = {
                "bundle_slug": input.bundle_slug,
                "prefix": input.prefix or "",
            }
        elif input.op == SecretChangeProposal.Op.DETACH_BUNDLE.value:
            if input.attachment_id is None:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "attachmentId is required for op=detach_bundle",
                    field="attachmentId",
                )
            ref = (
                AppSecretBundleRef.objects.select_related("app_environment")
                .filter(
                    guid=str(input.attachment_id),
                    registered_app__organization_id=_caller_org_id(),
                    deleted_at__isnull=True,
                )
                .first()
            )
            if ref is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "attachment not found",
                    field="attachmentId",
                )
            payload = {"attachment_id": str(ref.guid)}
            env_name = env_name or ref.app_environment.name
        else:  # pragma: no cover — guarded above
            return gql_failure(ErrorCode.VALIDATION.value, "unknown op", field="op")

        actor = _actor_user(info)
        env_row = None
        if env_name:
            env_row = AppEnvironment.objects.filter(
                registered_app=app,
                name=env_name,
                deleted_at__isnull=True,
            ).first()
        diff = build_diff(
            app=app,
            op=input.op,
            payload=payload,
            environment_name=env_name,
        )
        proposal = SecretChangeProposal.objects.create(
            registered_app=app,
            app_environment=env_row,
            environment_name=env_name or "",
            proposer=actor,
            op=input.op,
            payload=payload,
            payload_diff=diff,
            required_approver_count=max(int(app.secret_minimum_approvals or 1), 1),
            expires_at=timezone.now() + timedelta(seconds=_proposal_ttl_seconds()),
            created_by=actor,
            updated_by=actor,
        )
        return gql_success(secret_change_proposal_to_type(proposal, info=info))

    @strawberry.field
    @mutation_audit(
        action="app.secret.proposal.approve",
        target=_proposal_target_from_input,
    )
    @require_permission(
        Permission.SECRET_APPROVE, scope=secret_change_proposal_app_scope("input.proposal_id")
    )
    @tenant_scoped()
    def approve_secret_change(
        self,
        info: Info,
        input: ApproveSecretChangeInput,
    ) -> MutationResultType[SecretChangeProposalType]:
        proposal = (
            SecretChangeProposal.objects.select_related("registered_app")
            .filter(
                guid=str(input.proposal_id),
                registered_app__organization_id=_caller_org_id(),
                deleted_at__isnull=True,
            )
            .first()
        )
        if proposal is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "proposal not found")
        if proposal.status != SecretChangeProposal.Status.PENDING.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"proposal is in status {proposal.status}, expected pending",
            )
        # TTL gate — refuse to approve an expired proposal even if
        # the sweeper hasn't transitioned it yet (race window).
        if proposal.expires_at <= timezone.now():
            with transaction.atomic():
                proposal.transition_to(SecretChangeProposal.Status.EXPIRED)
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "proposal has expired",
            )

        actor = _actor_user(info)
        if actor is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "authenticated user required to approve",
            )
        # Self-approval gate (#488 + mirror of ALLOW_SELF_APPROVE_DEPLOYS).
        if proposal.proposer_id == actor.pk and not _self_approve_secrets_allowed():
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cannot approve your own proposal — another approver required",
            )
        if not _is_eligible_secret_approver(proposal.registered_app, user_id=actor.pk):
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "you are not in this app's secret-approver set",
            )

        with transaction.atomic():
            # Re-vote is disallowed by the unique constraint; treat a
            # duplicate as a no-op (idempotent approve).
            existing = SecretChangeApproval.objects.filter(
                proposal=proposal,
                approver=actor,
                deleted_at__isnull=True,
            ).first()
            if existing is None:
                SecretChangeApproval.objects.create(
                    proposal=proposal,
                    approver=actor,
                    decision=SecretChangeApproval.Decision.APPROVED.value,
                    reason=(input.reason or "").strip(),
                    created_by=actor,
                    updated_by=actor,
                )
            approved_count = SecretChangeApproval.objects.filter(
                proposal=proposal,
                decision=SecretChangeApproval.Decision.APPROVED.value,
                deleted_at__isnull=True,
            ).count()
            if approved_count >= int(proposal.required_approver_count or 1):
                proposal.transition_to(SecretChangeProposal.Status.APPROVED)
                result = apply_proposal(proposal, actor=actor)
                if result.ok:
                    proposal.transition_to(SecretChangeProposal.Status.APPLIED)
                else:
                    proposal.apply_error = result.error
                    proposal.save(
                        update_fields=[
                            "apply_error",
                            "updated_at",
                            "version",
                        ]
                    )

        proposal.refresh_from_db()
        return gql_success(secret_change_proposal_to_type(proposal, info=info))

    @strawberry.field
    @mutation_audit(
        action="app.secret.proposal.reject",
        target=_proposal_target_from_input,
    )
    @require_permission(
        Permission.SECRET_APPROVE, scope=secret_change_proposal_app_scope("input.proposal_id")
    )
    @tenant_scoped()
    def reject_secret_change(
        self,
        info: Info,
        input: RejectSecretChangeInput,
    ) -> MutationResultType[SecretChangeProposalType]:
        reason = (input.reason or "").strip()
        if not reason:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "reason is required",
                field="reason",
            )
        proposal = (
            SecretChangeProposal.objects.select_related("registered_app")
            .filter(
                guid=str(input.proposal_id),
                registered_app__organization_id=_caller_org_id(),
                deleted_at__isnull=True,
            )
            .first()
        )
        if proposal is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "proposal not found")
        if proposal.status != SecretChangeProposal.Status.PENDING.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"proposal is in status {proposal.status}, expected pending",
            )
        actor = _actor_user(info)
        if actor is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "authenticated user required to reject",
            )
        # Proposer rejecting their own proposal would be equivalent to
        # withdrawal — redirect to that path for clarity in the audit
        # trail.
        if proposal.proposer_id == actor.pk:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "use withdrawSecretChange to retract your own proposal",
            )
        if not _is_eligible_secret_approver(proposal.registered_app, user_id=actor.pk):
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "you are not in this app's secret-approver set",
            )

        with transaction.atomic():
            existing = SecretChangeApproval.objects.filter(
                proposal=proposal,
                approver=actor,
                deleted_at__isnull=True,
            ).first()
            if existing is None:
                SecretChangeApproval.objects.create(
                    proposal=proposal,
                    approver=actor,
                    decision=SecretChangeApproval.Decision.REJECTED.value,
                    reason=reason,
                    created_by=actor,
                    updated_by=actor,
                )
            else:
                # #1214: the approver already had a vote row (typically
                # APPROVED) and is now rejecting. Skipping it left the
                # denormalized ApproverList showing them as 'approved' on a
                # proposal that just went to rejected. Flip the row in place —
                # update respects the unique-per-(proposal, approver)
                # constraint that a second create would violate.
                existing.decision = SecretChangeApproval.Decision.REJECTED.value
                existing.reason = reason
                existing.decided_at = timezone.now()
                existing.updated_by = actor
                existing.save(
                    update_fields=[
                        "decision",
                        "reason",
                        "decided_at",
                        "updated_by",
                        "updated_at",
                        "version",
                    ]
                )
            # ANY rejection moves the proposal to rejected — one nay
            # kills the proposal, mirroring the deploy quorum policy.
            proposal.transition_to(SecretChangeProposal.Status.REJECTED)

        proposal.refresh_from_db()
        return gql_success(secret_change_proposal_to_type(proposal, info=info))

    @strawberry.field
    @mutation_audit(
        action="app.secret.proposal.withdraw",
        target=_proposal_target_from_input,
    )
    @require_permission(Permission.APP_UPDATE, scope=secret_change_proposal_app_scope("input.proposal_id"))
    @tenant_scoped()
    def withdraw_secret_change(
        self,
        info: Info,
        input: WithdrawSecretChangeInput,
    ) -> MutationResultType[SecretChangeProposalType]:
        """Proposer-only retraction (#488).  Distinct from rejection so
        the audit trail records the difference between 'proposer
        changed their mind' and 'approver said no'."""
        proposal = (
            SecretChangeProposal.objects.select_related("registered_app")
            .filter(
                guid=str(input.proposal_id),
                registered_app__organization_id=_caller_org_id(),
                deleted_at__isnull=True,
            )
            .first()
        )
        if proposal is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "proposal not found")
        if proposal.status != SecretChangeProposal.Status.PENDING.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"proposal is in status {proposal.status}, expected pending",
            )
        actor = _actor_user(info)
        if actor is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "authenticated user required to withdraw",
            )
        if proposal.proposer_id != actor.pk:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "only the proposer can withdraw a proposal",
            )
        with transaction.atomic():
            proposal.transition_to(SecretChangeProposal.Status.WITHDRAWN)
        proposal.refresh_from_db()
        return gql_success(secret_change_proposal_to_type(proposal, info=info))
