"""The invitee's side of an invitation, before they are a member.

An invitee opens the link signed out, often with no Astrolift account at
all. Two things have to work for them before ``acceptInvitation`` can:

* **The preview.** ``POST /app/auth1/invitation-preview`` with
  ``{"token": ...}`` says what the link is for: its status, the organization,
  who sent it and the role, plus whether the browser's own session belongs to
  the invitee. The token is the only credential. An unknown or deleted token,
  or one whose organization is gone, reads as ``not_found`` with nothing else.
  The answer carries what the invitation email already says, the role, and a
  masked address; it writes nothing, so expiry is read off ``expires_at``
  rather than recorded.
* **The account.** auth1 finds a signed-in person's account by email. When
  there is none but the IdP-verified address holds a live invitation,
  :func:`invited_account` creates it: the invitation is an administrator's
  say-so that this address should have one. It grants nothing else; joining
  still takes the explicit accept with the token.
"""

from __future__ import annotations

import hashlib
import json
import logging

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from core.mutations import AuditEntry, emit_audit
from core.permissions import route_auth

logger = logging.getLogger(__name__)

_MAX_TOKEN_LENGTH = 256
# Django's ``User.username`` column; the account is keyed on the address.
_MAX_USERNAME_LENGTH = 150
_NAME_LENGTH = 150


def mask_email(email: str) -> str:
    """``j•••@example.com``: enough for the invitee to recognise the address,
    not enough to hand a stranger holding the link the whole of it."""
    local, _, domain = (email or "").partition("@")
    if not local or not domain:
        return "•••"
    return f"{local[0]}•••@{domain}"


def _status(invitation) -> str:
    from astrolift_identity.models import Invitation

    if invitation.status == Invitation.Status.PENDING:
        return "expired" if invitation.is_expired else "pending"
    return {
        Invitation.Status.ACCEPTED: "accepted",
        Invitation.Status.EXPIRED: "expired",
        Invitation.Status.REVOKED: "revoked",
    }.get(invitation.status, "not_found")


def _viewer(user, invited_email: str | None) -> dict:
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return {"signedIn": False}
    email = (user.email or "").strip()
    return {
        "signedIn": True,
        "email": email,
        "isInvitee": bool(email and invited_email and email.lower() == invited_email.lower()),
    }


def preview(token: str, user) -> dict:
    """What the invitation ``token`` names, as the invitation page shows it."""
    from astrolift_identity.emails import _resolve_inviter_name
    from astrolift_identity.models import Invitation, Organization

    digest = hashlib.sha256(token.encode()).hexdigest()
    invitation = (
        Invitation.objects.filter(token_hash=digest, scope_kind=Invitation.ScopeKind.ORG)
        .select_related("role", "invited_by")
        .first()
    )
    organization = (
        Organization.objects.filter(pk=invitation.scope_id).only("name").first() if invitation else None
    )
    if invitation is None or organization is None:
        return {"status": "not_found", "viewer": _viewer(user, None)}
    return {
        "status": _status(invitation),
        "invitation": {
            "organizationName": organization.name,
            "inviterName": _resolve_inviter_name(invitation.invited_by),
            "roleName": invitation.role.name if invitation.role is not None else None,
            "emailHint": mask_email(invitation.email),
            "expiresAt": invitation.expires_at.isoformat(),
        },
        "viewer": _viewer(user, invitation.email),
    }


@route_auth(
    credential="invitation token",
    scope=(
        "the one invitation the token names: its organization name, inviter, role, "
        "masked address and expiry; the caller's own sign-in state"
    ),
)
@never_cache
def invitation_preview(request):
    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)
    try:
        body = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"detail": "invalid JSON body"}, status=400)
    token = body.get("token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not 0 < len(token) <= _MAX_TOKEN_LENGTH:
        return JsonResponse({"detail": "token required"}, status=400)
    return JsonResponse(preview(token, getattr(request, "user", None)))


def live_invitations(email: str):
    """Pending, unexpired invitations for ``email`` into organizations that still exist."""
    from astrolift_identity.models import Invitation, Organization

    return Invitation.objects.filter(
        email__iexact=email,
        scope_kind=Invitation.ScopeKind.ORG,
        status=Invitation.Status.PENDING,
        expires_at__gt=timezone.now(),
        scope_id__in=Organization.objects.values("pk"),
    )


def invited_account(email: str, *, first_name: str = "", last_name: str = ""):
    """Create the account a live invitation was sent to, or return None.

    The caller has already looked the address up and found no account, and
    the IdP has verified it. The account gets an unusable password, the way
    auto-signup creates one: it signs in through the IdP only. An existing
    username equal to the address is never taken over.
    """
    email = (email or "").strip()
    if "@" not in email or len(email) > _MAX_USERNAME_LENGTH:
        return None
    invitations = list(live_invitations(email))
    if not invitations:
        return None
    User = get_user_model()
    user = User(
        username=email,
        email=email,
        first_name=(first_name or "")[:_NAME_LENGTH],
        last_name=(last_name or "")[:_NAME_LENGTH],
    )
    user.set_unusable_password()
    try:
        with transaction.atomic():
            user.save()
    except IntegrityError:
        # The username is taken. A sign-in racing this one may have created
        # this very account; anything else holding the name stays its own.
        logger.warning("invited account not created: username %r is taken", email)
        return User.objects.filter(email__iexact=email).first()
    for invitation in invitations:
        emit_audit(
            AuditEntry(
                actor_user_id=user.pk,
                organization_id=invitation.scope_id,
                action="auth.invitation.account",
                decision="ALLOW",
                target_kind="Invitation",
                target_id=str(invitation.guid),
                duration_ms=0,
                permissions=(),
                extra={"user_id": user.pk},
            )
        )
    logger.info("created the invited account user=%s for %d live invitation(s)", user.pk, len(invitations))
    return user
