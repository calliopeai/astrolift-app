"""InvitationMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.db.models import Q
from strawberry.types import Info

from astrolift_graphql import (
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_graphql import (
    success as gql_success,
)
from astrolift_identity.models import (
    Invitation,
    Member,
    Organization,
    Role,
    RoleBinding,
)
from astrolift_identity.schema.mutations.helpers import (
    _actor,
    _make_token_secret,
)
from astrolift_identity.schema.mutations.types import (
    AcceptInvitationInput,
    CreateInvitationInput,
    ResendInvitationInput,
    RevokeInvitationInput,
)
from astrolift_identity.schema.types import (
    InvitationCreatedType,
    InvitationType,
    invitation_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class InvitationMutations:
    # ---- Invitations -----------------------------------------------------

    @strawberry.field
    @mutation_audit(action="invitation.create")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def create_invitation(
        self, info: Info, input: CreateInvitationInput
    ) -> MutationResultType[InvitationCreatedType]:
        """Issue an invitation token for an email address.

        The plaintext token is returned exactly once via the
        InvitationCreatedType payload — the DB stores only its
        SHA-256 hash. An invitation email is dispatched to ``email``
        with the accept link; email delivery is best-effort and the
        copy-link affordance in the UI is the durable channel — a
        send failure logs but does not fail the mutation.
        """
        from datetime import timedelta

        from django.utils import timezone

        from astrolift_identity.emails import (
            build_invitation_accept_url,
            send_invitation_email,
        )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        email = input.email.strip().lower()
        if not email or "@" not in email:
            return gql_failure(ErrorCode.VALIDATION.value, "valid email required", field="email")

        role = None
        if input.role_slug:
            # Only the caller org's custom roles or a system/null-org role
            # may be attached — a foreign org's custom role slug reads as
            # not-found.
            role = (
                Role.objects.filter(slug=input.role_slug)
                .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
                .first()
            )
            if role is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "role not found", field="roleSlug")

        # Reject if there's already an active pending invite for the
        # same email at this scope — re-sending should go through a
        # separate "rotate token" flow rather than silently doubling
        # up rows.
        if Invitation.objects.filter(
            email=email,
            scope_kind=Invitation.ScopeKind.ORG,
            scope_id=org_id,
            status=Invitation.Status.PENDING,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"a pending invitation already exists for {email}",
                field="email",
            )

        plaintext, digest, _last4 = _make_token_secret()
        expires_at = timezone.now() + timedelta(
            days=int(input.expires_in_days) if input.expires_in_days else 7
        )
        inv = Invitation.objects.create(
            email=email,
            scope_kind=Invitation.ScopeKind.ORG,
            scope_id=org_id,
            role=role,
            token_hash=digest,
            expires_at=expires_at,
            invited_by=_actor(),
            status=Invitation.Status.PENDING,
        )

        # Best-effort delivery. The accept_url_path returned in the
        # payload remains the durable copy-link affordance regardless
        # of whether the email actually goes out.
        org = Organization.objects.filter(pk=org_id).only("name").first()
        org_name = org.name if org is not None else "Astrolift"
        send_invitation_email(
            to_email=email,
            org_name=org_name,
            inviter=_actor(),
            accept_url=build_invitation_accept_url(plaintext),
            expires_at=expires_at,
        )

        return gql_success(
            InvitationCreatedType(
                invitation=invitation_to_type(inv),
                plaintext_token=plaintext,
                accept_url_path=f"/auth/invitation/{plaintext}",
            )
        )

    @strawberry.field
    @mutation_audit(action="invitation.revoke")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def revoke_invitation(
        self, info: Info, input: RevokeInvitationInput
    ) -> MutationResultType[InvitationType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        inv = Invitation.objects.filter(
            guid=str(input.id),
            scope_kind=Invitation.ScopeKind.ORG,
            scope_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if inv is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "invitation not found")
        if inv.status not in (Invitation.Status.PENDING,):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"invitation is already {inv.status}",
            )
        inv.status = Invitation.Status.REVOKED
        inv.save(update_fields=["status", "updated_at", "version"])
        return gql_success(invitation_to_type(inv))

    @strawberry.field
    @mutation_audit(action="invitation.resend")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def resend_invitation(
        self, info: Info, input: ResendInvitationInput
    ) -> MutationResultType[InvitationCreatedType]:
        """Re-send a pending invitation with a freshly-rotated token.

        Only a PENDING invitation in the caller's own org can be
        resent; an accepted / revoked / already-expired-status
        invitation — or a guid owned by another org — reads as
        not-found or precondition and never mutates.

        The plaintext token is never persisted (only its SHA-256 hash
        is), so the original link cannot be re-sent: resend *always*
        mints a fresh token and refreshes ``expires_at`` to a new
        window. That invalidates any previously-issued link for this
        invitation, which is also the desired security property — a
        resend supersedes the old link. The new plaintext + accept URL
        come back in the ``InvitationCreatedType`` payload exactly
        once, preserving the copy-link durable channel exactly as the
        create path does. Email delivery stays best-effort and never
        fails the mutation.
        """
        from datetime import timedelta

        from django.utils import timezone

        from astrolift_identity.emails import (
            build_invitation_accept_url,
            send_invitation_email,
        )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # Scope the lookup to the caller's own org — a foreign-org guid
        # (or a missing tenant) resolves to None and reads as not-found,
        # so a resend can never reach across tenants.
        inv = Invitation.objects.filter(
            guid=str(input.id),
            scope_kind=Invitation.ScopeKind.ORG,
            scope_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if inv is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "invitation not found")
        if inv.status != Invitation.Status.PENDING:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"invitation is {inv.status}; only a pending invitation can be resent",
            )

        # Rotate the token (the old link dies) and refresh the window so
        # the freshly-sent link is always valid.
        plaintext, digest, _last4 = _make_token_secret()
        expires_at = timezone.now() + timedelta(days=7)
        inv.token_hash = digest
        inv.expires_at = expires_at
        inv.save(update_fields=["token_hash", "expires_at", "updated_at", "version"])

        # Best-effort delivery. The accept_url_path returned in the
        # payload is the durable copy-link affordance regardless of
        # whether the email actually goes out.
        org = Organization.objects.filter(pk=org_id).only("name").first()
        org_name = org.name if org is not None else "Astrolift"
        send_invitation_email(
            to_email=inv.email,
            org_name=org_name,
            inviter=_actor(),
            accept_url=build_invitation_accept_url(plaintext),
            expires_at=expires_at,
        )

        return gql_success(
            InvitationCreatedType(
                invitation=invitation_to_type(inv),
                plaintext_token=plaintext,
                accept_url_path=f"/auth/invitation/{plaintext}",
            )
        )

    # By definition the accepting user has no tenant context yet at
    # the moment they click the link. The token *is* the auth check.
    # Listed in the tenancy guardrail's EXEMPT set with this rationale.
    @strawberry.field
    @mutation_audit(action="invitation.accept")
    def accept_invitation(
        self, info: Info, input: AcceptInvitationInput
    ) -> MutationResultType[InvitationType]:
        import hashlib

        from django.utils import timezone

        request = getattr(info.context, "request", None)
        user = getattr(request, "user", None) if request else None
        if user is None or not getattr(user, "is_authenticated", False):
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "sign in before accepting an invitation",
            )

        digest = hashlib.sha256(input.token.encode()).hexdigest()
        inv = Invitation.objects.filter(token_hash=digest, deleted_at__isnull=True).first()
        if inv is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "invitation not found")
        if inv.status != Invitation.Status.PENDING:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"invitation is {inv.status}; nothing to accept",
            )
        if inv.is_expired:
            inv.status = Invitation.Status.EXPIRED
            inv.save(update_fields=["status", "updated_at", "version"])
            return gql_failure(ErrorCode.PRECONDITION.value, "invitation has expired")

        # Match the invite to a user. We require the invited email to
        # match the caller's email so an invite leaked to another user
        # can't be redeemed against a different account.
        if (user.email or "").lower() != inv.email.lower():
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "invitation email does not match your account",
            )

        # Idempotency: a concurrent accept would insert a duplicate
        # Member row; the unique constraint on (user, scope_kind,
        # scope_id) catches that case at the DB level. We swallow the
        # IntegrityError to a clean precondition error.
        from django.db import IntegrityError, transaction

        try:
            with transaction.atomic():
                Member.objects.create(
                    user=user,
                    scope_kind=inv.scope_kind,
                    scope_id=inv.scope_id,
                    is_active=True,
                    lifecycle=Member.Lifecycle.ACTIVE,
                    joined_at=timezone.now(),
                )
                if inv.role_id and inv.scope_kind == Invitation.ScopeKind.ORG:
                    RoleBinding.objects.create(
                        user=user,
                        role_id=inv.role_id,
                        scope_kind=RoleBinding.ScopeKind.ORG,
                        scope_id=inv.scope_id,
                    )
                inv.status = Invitation.Status.ACCEPTED
                inv.accepted_at = timezone.now()
                inv.save(update_fields=["status", "accepted_at", "updated_at", "version"])
        except IntegrityError:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "you are already a member of this scope",
            )

        return gql_success(invitation_to_type(inv))
