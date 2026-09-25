"""
First-class ``astrolift_identity`` surface for right-to-delete (#312).

Wraps the existing ``core/anonymization.py`` + ``Profile.anonymize_user``
helpers behind a structured GraphQL mutation so operator UIs (the
``/members`` page in #308) don't have to fall through to the legacy
``profile_request_delete_user`` mutation on the core Graphene/Strawberry
seam.

Composition matches the connected_accounts pattern: this module exports
a thin ``IdentityAnonymizeUserMutation`` type that ``schema/__init__.py``
folds into ``IdentityMutation`` via multiple inheritance — no edit to
``config.schema`` needed.

Permission model:

* **Self-anonymization** — any authenticated user can anonymize their
  own account. Mirrors the legacy core behaviour and aligns with GDPR
  Art. 17 (the data subject is always entitled to request erasure of
  their own data). The payload sets ``requires_logout=True`` so the FE
  can drop the session.

* **Anonymizing another user** — requires ``Permission.ORG_MANAGE_MEMBERS``
  (``org.manage_members``). This is the same gate the FE's ``Can``
  guard surfaces on the affordance and the same gate the rest of the
  member-management mutations in this module use; introducing a brand
  new ``USER_ANONYMIZE`` perm would duplicate it without adding
  segregation. The gate is held in the active org, and the user row it
  acts on is global, so the target must also be that org's to erase
  (#1979): a member of the active org, not the platform operator, not
  an active member of any other org, and holding nothing there the
  caller could not grant. The platform operator skips the last three.

Idempotency:

A user that's already anonymized (detected by the marker email suffix
``@anon-astrolift.net`` that ``Profile.anonymize_user`` writes, or by
``is_active=False`` plus a matching placeholder username) returns
``ok=True`` with ``was_self`` reflecting whether the actor *would* be
anonymizing themselves and ``requires_logout=False`` — there's no
session to drop on an already-deactivated account.

Audit:

``@mutation_audit(action="identity.user.anonymized")`` records actor +
decision regardless of outcome. The payload here intentionally carries
only opaque PKs / GIDs so the audit row itself doesn't accumulate PII
that would just need scrubbing on the next anonymization pass.
"""

from __future__ import annotations

import datetime as dt

import strawberry
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.grants import require_grantable
from astrolift_identity.models import Member
from astrolift_identity.permission_resolver import _org_confined_bindings
from astrolift_identity.step_up import requires_elevation
from core.models import Profile
from core.mutations import ErrorCode, mutation_audit
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    check_permission,
    is_platform_operator,
)
from core.tenancy import TenantContext, get_current_tenant

# Marker substring written into ``user.email`` by
# ``Profile.anonymize_user`` (see ``core/models/user.py``). Used here
# to detect the idempotent "already anonymized" case without adding a
# new schema column.
_ANON_EMAIL_MARKER = "@anon-astrolift.net"


# ---------------------------------------------------------------------------
# GraphQL types
# ---------------------------------------------------------------------------


@strawberry.input
class AstroliftAnonymizeUserInput:
    """Target user identifier.

    ``user_gid`` accepts the User's primary key as a string (matches
    the ``id`` field on ``AstroliftUser`` returned by the member /
    role-binding queries). The legacy core model doesn't carry a UUID
    on ``auth.User``, so we use the stable PK rather than minting a
    parallel identifier just for this surface.
    """

    user_gid: GUID


@strawberry.type(name="AstroliftAnonymizeUserPayload")
class AstroliftAnonymizeUserPayload:
    """Result of an anonymize operation.

    ``requires_logout`` is the signal the FE uses to drop the current
    session — it's only ``True`` when the actor anonymized themselves
    AND a new logout actually needs to happen (i.e. they weren't
    already an anonymized account, which would mean no live session
    anyway).
    """

    anonymized_user_id: GUID
    was_self: bool
    requires_logout: bool
    lifecycle: str
    anonymized_at: dt.datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _viewer(info: Info):
    request = getattr(info.context, "request", None)
    user = getattr(request, "user", None) if request else None
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


def _resolve_target_user(user_gid: str):
    """Resolve a User by stringified PK.

    Returns ``None`` for malformed input rather than raising — the
    caller surfaces NOT_FOUND either way and we don't want a stray
    non-numeric value to escape as INTERNAL.
    """
    User = get_user_model()
    try:
        pk = int(str(user_gid))
    except (TypeError, ValueError):
        return None
    return User.objects.filter(pk=pk).first()


def _is_already_anonymized(user) -> bool:
    """Detect a user that's already been through ``Profile.anonymize_user``.

    The marker email suffix is the most reliable signal — the helper
    always rewrites ``user.email`` to ``<short_uuid>@anon-astrolift.net``
    and there's no other code path in the platform that produces that
    suffix. Falling back on ``is_active`` alone would over-match
    (suspended accounts also flip to ``is_active=False`` without
    losing PII).
    """
    return bool(user.email and _ANON_EMAIL_MARKER in user.email) and not user.is_active


def _require_may_anonymize(viewer, target) -> None:
    """Refuse erasing ``target`` on behalf of the active org (#1979).

    ``org.manage_members`` is held in one org, and the row this erases is
    the install-wide user. Every refusal raises :class:`PermissionDenied`
    so ``@mutation_audit`` records a DENY rather than an ALLOW (#1968).
    """

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        raise PermissionDenied(Permission.ORG_MANAGE_MEMBERS, None, "no active organization")
    scope = PermissionScope(kind=ScopeKind.ORG, id=org_id)
    # At org scope: a binding on one team, with that team selected, would
    # satisfy a targetless check, and erasing reaches the whole org.
    check_permission(Permission.ORG_MANAGE_MEMBERS, scope=scope)

    def refuse(reason: str) -> PermissionDenied:
        return PermissionDenied(Permission.ORG_MANAGE_MEMBERS, scope, reason)

    # A deactivated membership still counts: erasure after offboarding is
    # the usual right-to-delete request.
    org_memberships = Member.objects.filter(user_id=target.pk, scope_kind=Member.ScopeKind.ORG)
    if not org_memberships.filter(scope_id=org_id).exists():
        raise refuse("user is not a member of this organization")

    if is_platform_operator(viewer):
        return
    if target.is_superuser:
        raise refuse("only the platform operator can anonymize a platform operator account")
    if org_memberships.filter(is_active=True).exclude(scope_id=org_id).exists():
        raise refuse("user is also an active member of another organization")
    held: set[str] = set()
    for binding in _org_confined_bindings(TenantContext(organization_id=org_id, actor_user_id=target.pk)):
        held.update(binding.role.permissions or ())
    # Erasing someone ends every access they hold here, so it is capped
    # like handing that access out (#1964).
    require_grantable(held, scope_kind="ORG", scope_id=org_id, gate=Permission.ORG_MANAGE_MEMBERS)


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


@strawberry.type
class IdentityAnonymizeUserMutation:
    @strawberry.field
    @mutation_audit(
        action="identity.user.anonymized",
        target=lambda self, info, input: ("user", str(input.user_gid)),
    )
    @requires_elevation(action_label="identity.user.anonymize")
    def astrolift_anonymize_user(
        self, info: Info, input: AstroliftAnonymizeUserInput
    ) -> MutationResultType[AstroliftAnonymizeUserPayload]:
        """Anonymize a user's PII while preserving the row for audit-trail
        referential integrity.

        Returns ``ok=true`` even when the target user was already
        anonymized — operators (and the FE) treat this as a successful
        no-op rather than a confusing error.
        """
        viewer = _viewer(info)
        if viewer is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        target = _resolve_target_user(str(input.user_gid))
        if target is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "user not found",
                field="userGid",
            )

        is_self = target.pk == viewer.pk

        # Permission gate: self-anonymization is free; anonymizing
        # someone else requires the org-level member-management perm.
        # We don't use the ``@require_permission`` decorator here
        # because it would enforce the check on the self path too,
        # which we explicitly want to allow.
        if not is_self:
            _require_may_anonymize(viewer, target)

        # Idempotency: if the user is already anonymized, return the
        # current state with ``requires_logout=False``. Don't run the
        # anonymizer a second time — it would overwrite the existing
        # short_uuid placeholders and pointlessly churn the row.
        if _is_already_anonymized(target):
            return gql_success(
                AstroliftAnonymizeUserPayload(
                    anonymized_user_id=GUID(str(target.pk)),
                    was_self=is_self,
                    requires_logout=False,
                    lifecycle=Member.Lifecycle.DEACTIVATED.value,
                    anonymized_at=target.profile.updated_at
                    if hasattr(target, "profile") and target.profile is not None
                    else timezone.now(),
                )
            )

        anonymized_at = timezone.now()
        with transaction.atomic():
            Profile.anonymize_user(target)
            # Flip every Member row for this user (org-wide, all
            # scopes) to ``deactivated`` — the spec asks for an
            # ``anonymized`` lifecycle but the existing enum doesn't
            # carry that value and adding one would require a migration
            # outside the scope of this issue; ``deactivated`` matches
            # the user's new ``is_active=False`` state and is the
            # closest existing value. Member rows aren't soft-deleted
            # here because the audit trail still references them.
            Member.objects.filter(user_id=target.pk, deleted_at__isnull=True).update(
                lifecycle=Member.Lifecycle.DEACTIVATED.value,
                is_active=False,
            )

        return gql_success(
            AstroliftAnonymizeUserPayload(
                anonymized_user_id=GUID(str(target.pk)),
                was_self=is_self,
                requires_logout=is_self,
                lifecycle=Member.Lifecycle.DEACTIVATED.value,
                anonymized_at=anonymized_at,
            )
        )


# Re-export for ``schema/__init__.py`` to fold into IdentityMutation.
__all__ = [
    "AstroliftAnonymizeUserInput",
    "AstroliftAnonymizeUserPayload",
    "IdentityAnonymizeUserMutation",
]
