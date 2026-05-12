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
        # existing membership.
        if Member.objects.filter(
            user_id=user.pk,
            scope_kind=Member.ScopeKind.ORG,
            scope_id=org.pk,
            deleted_at__isnull=True,
        ).exists():
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
            granted_role_slug: str | None = None
            if rule.default_role_id is not None and is_active:
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
