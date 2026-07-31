"""
Auto-join trusted email domains on OIDC callback.

After a successful sign-in we check whether the freshly-authenticated
user already belongs to the active organization. If not, and their
email's domain lives on the org's allowlist
(``OrganizationAllowlistedDomain``), we provision a ``Member`` row
(plus an optional ``RoleBinding`` when ``default_role`` is set) so the
user can use the platform without an explicit invitation.

The function never raises — auth flow is the priority, allowlist
auto-join is an enhancement on top of it. Every decision goes
through the standard audit log so operators can reconstruct who got
auto-joined and why.
"""

from __future__ import annotations

import logging

from core.mutations import AuditEntry, emit_audit

logger = logging.getLogger(__name__)


def _email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower()


def _active_organization():
    """Pick the install's active org.

    Mirrors auth1.active_idp.active_idp: single-tenant-per-install,
    "first org by created_at" is the implicit active org for unauth
    callers. Multi-org installs need a host-disambiguation hook
    here, same as the active-idp endpoint.
    """
    from astrolift_identity.models import Organization

    return Organization.objects.order_by("created_at").first()


def _audit(
    *,
    actor_user_id: int | None,
    organization_id: int | None,
    decision: str,
    error_code: str | None = None,
    error_message: str | None = None,
    extra: dict | None = None,
) -> None:
    emit_audit(
        AuditEntry(
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            action="auth.auto_join.allowlist",
            decision=decision,
            target_kind="Organization",
            target_id=organization_id,
            duration_ms=0,
            permissions=(),
            error_code=error_code,
            error_message=error_message,
            extra=extra,
        )
    )


def _resolve_pending_invitations(user, org, *, member_is_active: bool) -> bool:
    """Consume pending invitations fulfilled by an SSO join (#1228).

    Domain-allowlist auto-join and explicit invitations are two doors
    into the same room: when a user walks through the SSO door, any
    pending org-scope invitation for their email has served its purpose.
    Left unresolved it sits "Pending" until expiry — confusing on the
    Members page, a dangling live accept token, and (worse) the invite's
    intended role silently never applies, so an invited org_owner lands
    as the allowlist default role instead.

    Marks matching invitations accepted and applies their role binding
    (skipped when the membership is pending review — same gate as the
    allowlist default role). Returns True when at least one invite ROLE
    was applied, so the caller can skip the allowlist default role — the
    invite's role is the operator's explicit intent and stacking the
    default on top just produces chip noise. Never raises; the caller's
    auth-first posture applies.
    """
    applied_role = False
    try:
        from django.utils import timezone

        from astrolift_identity.models import Invitation, RoleBinding

        email = (getattr(user, "email", None) or "").strip().lower()
        if not email:
            return
        pending = Invitation.objects.filter(
            email__iexact=email,
            scope_kind=Invitation.ScopeKind.ORG,
            scope_id=org.pk,
            status=Invitation.Status.PENDING,
            deleted_at__isnull=True,
        )
        for inv in pending:
            if inv.is_expired:
                inv.status = Invitation.Status.EXPIRED
                inv.save(update_fields=["status", "updated_at", "version"])
                continue
            if inv.role_id and member_is_active:
                RoleBinding.objects.get_or_create(
                    user=user,
                    role_id=inv.role_id,
                    scope_kind=RoleBinding.ScopeKind.ORG,
                    scope_id=org.pk,
                )
                applied_role = True
            inv.status = Invitation.Status.ACCEPTED
            inv.accepted_at = timezone.now()
            inv.save(update_fields=["status", "accepted_at", "updated_at", "version"])
            logger.info(
                "auto_join: resolved pending invitation %s for user=%s (role_id=%s)",
                inv.pk,
                user.pk,
                inv.role_id,
            )
    except Exception:
        logger.exception(
            "auto_join: invitation resolution failed for user=%s — auth flow continues",
            getattr(user, "pk", None),
        )
    return applied_role


def maybe_auto_join_user(user) -> bool:
    """Auto-create a ``Member`` for ``user`` when their email's domain
    is on the active org's allowlist. Returns True when a Member was
    created (or already existed and we touched nothing), False on
    skip / error. Never raises.

    The active org is picked by ``_active_organization``. The check
    is a no-op when:

    * ``user`` is missing or has no email;
    * no organization exists yet (fresh install);
    * a Member row for (user, org) already exists;
    * the email domain doesn't match any active allowlist row.
    """
    try:
        from django.db import transaction
        from django.utils import timezone

        from astrolift_identity.models import (
            Member,
            OrganizationAllowlistedDomain,
            RoleBinding,
        )

        if user is None or not getattr(user, "is_authenticated", False):
            return False
        email = (getattr(user, "email", None) or "").strip()
        domain = _email_domain(email)
        if domain is None:
            return False

        org = _active_organization()
        if org is None:
            return False

        # Already a member of this org? Skip silently — even if the
        # allowlist row would otherwise apply, we never disturb an
        # existing membership. Still resolve any pending invitation for
        # this email (#1228): an invite issued after the member joined —
        # or one that raced the join — has been fulfilled and must not
        # dangle as a live Pending token.
        existing = Member.objects.filter(
            user_id=user.pk,
            scope_kind=Member.ScopeKind.ORG,
            scope_id=org.pk,
            deleted_at__isnull=True,
        ).first()
        if existing is not None:
            _resolve_pending_invitations(user, org, member_is_active=bool(existing.is_active))
            return False

        rule = (
            OrganizationAllowlistedDomain.objects.filter(
                organization_id=org.pk,
                domain=domain,
                deleted_at__isnull=True,
            )
            .select_related("default_role")
            .first()
        )
        if rule is None:
            return False

        if rule.requires_review:
            lifecycle = Member.Lifecycle.PENDING_INVITE
            is_active = False
        else:
            lifecycle = Member.Lifecycle.ACTIVE
            is_active = True

        with transaction.atomic():
            member = Member.objects.create(
                user=user,
                scope_kind=Member.ScopeKind.ORG,
                scope_id=org.pk,
                lifecycle=lifecycle,
                is_active=is_active,
                joined_at=timezone.now() if is_active else None,
            )
            # Resolve invitations first (#1228): when the invite carried a
            # role, that is the operator's explicit intent — skip the
            # allowlist default role instead of stacking both.
            invite_role_applied = _resolve_pending_invitations(user, org, member_is_active=is_active)
            granted_role_slug: str | None = None
            if rule.default_role_id is not None and is_active and not invite_role_applied:
                # Skip the RoleBinding when the member is pending review —
                # giving them a binding before approval would defeat the
                # whole point of the review gate. The reviewer can flip
                # lifecycle + grant the role in the same review action.
                RoleBinding.objects.create(
                    user=user,
                    role=rule.default_role,
                    scope_kind=RoleBinding.ScopeKind.ORG,
                    scope_id=org.pk,
                )
                granted_role_slug = rule.default_role.slug

        _audit(
            actor_user_id=user.pk,
            organization_id=org.pk,
            decision="ALLOW",
            extra={
                "domain": domain,
                "lifecycle": lifecycle,
                "requires_review": rule.requires_review,
                "granted_role_slug": granted_role_slug,
                "member_guid": str(member.guid),
            },
        )
        logger.info(
            "auto_join: user=%s domain=%s org_id=%s lifecycle=%s role=%s",
            user.pk,
            domain,
            org.pk,
            lifecycle,
            granted_role_slug,
        )
        return True
    except Exception:
        logger.exception("auto_join: failed for user=%s — auth flow continues", getattr(user, "pk", None))
        return False
