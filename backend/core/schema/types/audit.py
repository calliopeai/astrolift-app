"""GraphQL types and queries for the mutation audit log."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import strawberry
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_graphql import PageType, clamp_limit, keyset_page, search_q


@strawberry.type
class AuditLogEntry:
    operation: str
    variables: strawberry.scalars.JSON
    success: bool
    errors: list[str]
    ip_address: Optional[str]
    timestamp: datetime
    username: Optional[str]


def _require_superuser(info: Info) -> None:
    """Gate the audit log on install-operator identity.

    #537: the resolver was described as "admin only" but enforced
    nothing — any authed user (and even anonymous, since there was
    no auth check either) could pull the entire mutation audit log,
    which contains variables from every mutation across all
    tenants (including, e.g., redacted-but-correlatable input
    payloads). Gate on superuser; cross-tenant inspection of the
    audit log is an install-operator concern.

    Lives here rather than inline in each resolver so the list field
    and its paginated sibling cannot drift apart on who may read the
    log — the two share one gate the same way they share one
    queryset builder.
    """
    user = info.context.user
    if not getattr(user, "is_authenticated", False):
        raise GraphQLError("Authentication required")
    if not getattr(user, "is_superuser", False):
        raise GraphQLError("Audit log access requires superuser")


def _audit_log_to_type(log) -> AuditLogEntry:
    return AuditLogEntry(
        operation=log.operation,
        variables=log.variables,
        success=log.success,
        errors=log.errors,
        ip_address=log.ip_address,
        timestamp=log.timestamp,
        username=log.user.username if log.user else None,
    )


@strawberry.type
class AuditLogQuery:
    def _audit_logs_qs(
        self,
        *,
        operation: str | None = None,
        user_id: str | None = None,
        search: str | None = None,
    ):
        """Filtered, unordered mutation-audit stream.

        Cross-tenant by design — ``MutationAuditLog`` carries no
        organization column and the surface is superuser-only (see
        ``_require_superuser``). Shared by the list field and its
        paginated sibling so the two can never disagree about what an
        audit row is. Ordering is deliberately not applied here —
        ``keyset_page`` imposes it from the seek key.
        """
        from core.schema.audit import MutationAuditLog

        qs = MutationAuditLog.objects.select_related("user")
        if operation:
            qs = qs.filter(operation__icontains=operation)
        if user_id:
            qs = qs.filter(user_id=user_id)
        if search:
            qs = qs.filter(search_q(search, "operation", "user__username", "user__email"))
        return qs

    @strawberry.field(
        description="Query mutation audit logs. Superuser only.",
        deprecation_reason=(
            "Caps at 200 rows with no way to reach the 201st. Use auditLogsPage."
        ),
    )
    def audit_logs(
        self,
        info: Info,
        operation: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[AuditLogEntry]:
        _require_superuser(info)

        # ``limit`` used to reach ``qs[:limit]`` unclamped, so
        # ``limit: 999999999`` served the whole table in one response
        # (#1235). ``clamp_limit`` bounds it at 200; ``auditLogsPage``
        # is how a caller reaches past that now.
        qs = self._audit_logs_qs(operation=operation, user_id=user_id).order_by("-timestamp", "-pk")
        return [_audit_log_to_type(log) for log in qs[: clamp_limit(limit)]]

    @strawberry.field(description="Cursor-paginated mutation audit log. Superuser only.")
    def audit_logs_page(
        self,
        info: Info,
        operation: Optional[str] = None,
        user_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        after: Optional[str] = None,
    ) -> PageType[AuditLogEntry]:
        """Cursor-paginated mutation audit log (#1235).

        Replaces ``auditLogs``, whose limit was passed straight through
        to the slice: the operator's only two options were an unbounded
        response or a truncated one, with no way to walk the tail.

        Seek key is ``(-timestamp, -pk)``. ``MutationAuditLog`` predates
        ``BaseCoreModel`` — it has no ``guid`` and no ``created_at``, so
        the sort column is its indexed ``timestamp`` and the tiebreak is
        the integer pk. Both are NOT NULL, which is what the walk needs.

        ``search`` matches the operation name and the acting user, the
        two things an operator hunts an audit trail by.
        """
        _require_superuser(info)

        page = keyset_page(
            self._audit_logs_qs(operation=operation, user_id=user_id, search=search),
            cursor=after,
            limit=limit,
            sort_field="timestamp",
            tiebreak_field="pk",
        )
        return page.map(_audit_log_to_type)
