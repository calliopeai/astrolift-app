"""SessionMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import (
    GUID,
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_graphql import (
    success as gql_success,
)
from astrolift_identity.models import (
    Member,
)
from astrolift_identity.schema.mutations.types import (
    LogoutAllSessionsInput,
    RevokeAstroliftSessionInput,
    _HeartbeatSessionPayload,
    _LogoutAllSessionsPayload,
    _RevokeAstroliftSessionPayload,
)
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission
from core.tenancy import get_current_tenant


@strawberry.type
class SessionMutations:
    @strawberry.field
    @mutation_audit(action="session.logout_all")
    def logout_all_sessions(
        self, info: Info, input: LogoutAllSessionsInput
    ) -> MutationResultType[_LogoutAllSessionsPayload]:
        """Revoke every active session bound to the signed-in viewer.

        Soft-revokes the matching ``AstroliftSession`` sidecars AND
        deletes the underlying ``django_session`` rows so the cookies
        fail auth on the next request. Defaults to keeping the
        current session active so the caller doesn't immediately
        bounce to /login.

        Self-only — every authenticated user can sign themselves
        out of their other devices; no permission gate.
        """
        from astrolift_identity.models import AstroliftSession, RevocationReason
        from astrolift_identity.sessions import revoke_session

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        current_key = getattr(getattr(request, "session", None), "session_key", None)
        kept_current = False
        revoked_count = 0
        qs = AstroliftSession.objects.filter(user=viewer)
        for row in qs:
            if input.keep_current and current_key and row.session_key == current_key:
                kept_current = True
                continue
            revoke_session(row=row, actor_user_id=viewer.pk, reason=RevocationReason.LOGOUT)
            revoked_count += 1

        return gql_success(
            _LogoutAllSessionsPayload(
                revoked_count=revoked_count,
                kept_current=bool(kept_current),
            )
        )

    @strawberry.field
    @mutation_audit(
        action="session.revoke",
        target=lambda self, info, input: ("astrolift_session", str(input.session_id)),
    )
    def revoke_astrolift_session(
        self, info: Info, input: RevokeAstroliftSessionInput
    ) -> MutationResultType[_RevokeAstroliftSessionPayload]:
        """Revoke a single AstroliftSession by GUID.

        Permission gate:

        * Caller owns the session → allowed.
        * Caller belongs to the active org AND holds ``ORG_MANAGE_MEMBERS``
          AND the target session belongs to a user in that org → allowed.
        * Otherwise denied.

        Refuses to revoke the caller's *own current* session — that
        path is ``logoutAllSessions`` with ``keep_current=False`` or
        a plain ``/logout``. Revoking your own current request
        would race the response that's holding the cookie.

        Idempotent: revoking an already-revoked row returns
        ``ok: true`` with ``revoked=False``.
        """
        from astrolift_identity.models import AstroliftSession, RevocationReason
        from astrolift_identity.sessions import revoke_session

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        row = AstroliftSession.all_objects.filter(guid=str(input.session_id)).first()
        if row is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "session not found")

        is_owner = row.user_id == viewer.pk
        is_org_admin = False
        if not is_owner:
            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            if org_id is not None:
                # The active-org check: caller must be a Member of the
                # active org AND hold ORG_MANAGE_MEMBERS AND the target
                # session must belong to a user in that same org. Member
                # rows are (scope_kind, scope_id) — the ORG-scoped row
                # is the one we want for cross-tenant gating.
                target_in_org = Member.objects.filter(
                    user_id=row.user_id,
                    scope_kind=Member.ScopeKind.ORG.value,
                    scope_id=org_id,
                    deleted_at__isnull=True,
                ).exists()
                caller_in_org = Member.objects.filter(
                    user_id=viewer.pk,
                    scope_kind=Member.ScopeKind.ORG.value,
                    scope_id=org_id,
                    deleted_at__isnull=True,
                ).exists()
                if target_in_org and caller_in_org:
                    from core.permissions import check_permission as _check

                    try:
                        _check(Permission.ORG_MANAGE_MEMBERS)
                        is_org_admin = True
                    except Exception:  # noqa: BLE001 — gate is best-effort
                        is_org_admin = False

        if not (is_owner or is_org_admin):
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "cannot revoke this session")

        if is_owner:
            current_key = getattr(getattr(request, "session", None), "session_key", None)
            if current_key and row.session_key == current_key:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "cannot revoke the current session via this mutation; use logout",
                )

        already_revoked = row.revoked_at is not None or row.deleted_at is not None
        reason = (input.reason or "").strip()
        revoke_session(
            row=row,
            actor_user_id=viewer.pk,
            reason=(RevocationReason.ADMIN_REVOKE if is_org_admin else RevocationReason.USER_REVOKE),
        )
        if reason and not already_revoked:
            # Operator-supplied reason — stash it as a free-form note
            # so the audit row can carry it without expanding the
            # vocabulary of RevocationReason tags.
            row.revocation_reason = (row.revocation_reason + ":" + reason[:48])[:64]
            row.save(update_fields=["revocation_reason", "updated_at", "version"])

        return gql_success(
            _RevokeAstroliftSessionPayload(
                id=GUID(str(row.guid)),
                revoked=not already_revoked,
            )
        )

    @strawberry.field
    @mutation_audit(action="session.heartbeat")
    def heartbeat_session(self, info: Info) -> MutationResultType[_HeartbeatSessionPayload]:
        """Refresh the current session's ``last_seen_at`` immediately.

        Bypasses the middleware's per-minute write throttle — the
        client is explicitly asserting liveness, so we honor the
        ping. Used by CLIs on periodic ticks and the mobile app on
        foreground events.

        Self-only — no permission gate. Returns the updated
        ``last_seen_at`` so the caller can confirm the write took.
        """
        from astrolift_identity.models import AstroliftSession
        from astrolift_identity.sessions import record_session, touch_last_seen

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        # record_session() find-or-creates the row; touch_last_seen
        # then bypasses the throttle.
        row = record_session(request)
        if row is None:
            current_key = getattr(getattr(request, "session", None), "session_key", None)
            if current_key:
                row = AstroliftSession.objects.filter(session_key=current_key, user=viewer).first()
        if row is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "no active session to heartbeat")
        touch_last_seen(row)
        return gql_success(
            _HeartbeatSessionPayload(
                id=GUID(str(row.guid)),
                last_seen_at=row.last_seen_at,
            )
        )
