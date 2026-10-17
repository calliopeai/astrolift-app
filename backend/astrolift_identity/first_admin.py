"""The first administrator's claim (installer #503).

``bootstrap_admin`` leaves a pending :class:`FirstAdministrator` naming the
operator's email. auth1 calls :func:`claim` once per sign-in. The first
sign-in whose provider-verified email matches takes the row: the account is
created if there is none, bound ``org_owner`` on the organization, made an
active member, and the provider subject and time are recorded with an
``identity.first_admin.claimed`` audit event. The update is conditional on
``claimed_at IS NULL``, so of two concurrent sign-ins only one wins, and a
claimed row is never claimed again.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.mutations import AuditEntry, emit_audit

logger = logging.getLogger(__name__)


def normalise(email: str | None) -> str:
    return (email or "").strip().lower()


def _account(email: str):
    User = get_user_model()
    user = User.objects.filter(email__iexact=email).first()
    if user is not None:
        return user
    user = User(username=email, email=email)
    user.set_unusable_password()
    try:
        with transaction.atomic():
            user.save()
    except IntegrityError:
        # A sign-in racing this one created it, or the username is someone
        # else's and stays theirs.
        logger.warning("first administrator account not created: username %r is taken", email)
        return User.objects.filter(email__iexact=email).first()
    return user


def claim(user, provider_subject: str, email: str | None, email_verified: bool):
    """Claim a pending first administrator for this sign-in, if it is theirs.

    Returns the account the sign-in resolves to: ``user`` unchanged when
    nothing is claimed, or the claimed account (created when ``user`` is None).
    """
    from astrolift_identity.models import FirstAdministrator, Member, Role, RoleBinding

    email = normalise(email)
    if email_verified is not True or not email:
        return user
    pending_rows = list(FirstAdministrator.objects.filter(email=email, claimed_at__isnull=True))
    if not pending_rows:
        return user
    user = user or _account(email)
    if user is None:
        return None
    for pending in pending_rows:
        now = timezone.now()
        won = FirstAdministrator.objects.filter(pk=pending.pk, claimed_at__isnull=True).update(
            claimed_at=now, claimed_subject=provider_subject, claimed_user=user
        )
        if not won:
            continue
        if pending.is_superuser and not user.is_superuser:
            user.is_staff = user.is_superuser = True
            user.save(update_fields=["is_staff", "is_superuser"])
        org_owner = Role.objects.get(slug="org_owner", is_system=True, organization=None)
        RoleBinding.objects.get_or_create(
            user=user, role=org_owner, scope_kind="ORG", scope_id=pending.organization_id
        )
        Member.objects.update_or_create(
            user=user,
            scope_kind="ORG",
            scope_id=pending.organization_id,
            defaults={"is_active": True, "lifecycle": "active"},
        )
        emit_audit(
            AuditEntry(
                actor_user_id=user.pk,
                organization_id=pending.organization_id,
                action="identity.first_admin.claimed",
                decision="ALLOW",
                target_kind="FirstAdministrator",
                target_id=pending.pk,
                duration_ms=0,
                permissions=(),
                extra={"email": email, "subject": provider_subject, "claimed_at": now.isoformat()},
            )
        )
        logger.info("first administrator claimed: org=%s user=%s", pending.organization_id, user.pk)
    return user
