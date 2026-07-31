"""auditLogsPage — cursor pagination over the mutation audit log (#1235).

``auditLogs`` handed its ``limit`` straight to ``qs[:limit]`` with no
clamp, so a superuser client could ask for ``limit: 999999999`` and get
the whole ``MutationAuditLog`` table serialised into one response. The
only other option was a truncated list with no way to reach the tail:
an install operator investigating an incident could see the newest 50
mutations or all of them, and nothing in between.

These tests pin both halves of the fix — the clamp on the legacy field,
and a walk that reaches every row — plus the two properties of this
surface that are deliberately unlike every other converted resolver:
it is superuser-only, and it is cross-tenant on purpose.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from graphql import GraphQLError

from astrolift_graphql import MAX_PAGE_LIMIT
from core.schema.audit import MutationAuditLog
from core.schema.types.audit import AuditLogQuery

pytestmark = pytest.mark.django_db

User = get_user_model()


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=None))


@pytest.fixture
def operator(db):
    """The install operator: the only identity that may read this log."""
    return User.objects.create_superuser(
        username="audit-root-1235",
        email="audit-root-1235@example.test",
        password="x",
    )


def _log(operation: str, *, user=None, success: bool = True) -> MutationAuditLog:
    return MutationAuditLog.objects.create(
        operation=operation,
        variables={"op": operation},
        success=success,
        errors=[],
        user=user,
    )


def _db_order(**filters) -> list[str]:
    """The operations in the DB's own seek order.

    ``timestamp`` is ``auto_now_add``, so rows written in a tight loop
    can share one value — asserting against creation order would be
    asserting against a coincidence. This is the ordering the walk is
    contractually required to reproduce.
    """
    return list(
        MutationAuditLog.objects.filter(**filters)
        .order_by("-timestamp", "-pk")
        .values_list("operation", flat=True)
    )


def _walk(query: AuditLogQuery, user, *, limit: int, **kwargs) -> list[str]:
    """Page through the whole stream, returning every operation in order."""
    operations: list[str] = []
    cursor: str | None = None
    for _ in range(50):  # bounded so a non-terminating walk fails loudly
        page = query.audit_logs_page(_info(user), limit=limit, after=cursor, **kwargs)
        operations.extend(item.operation for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return operations
    raise AssertionError("walk did not terminate")


# ---------------------------------------------------------------------------
# The bug: an unclamped limit on the legacy field.
# ---------------------------------------------------------------------------


def test_legacy_field_no_longer_honours_an_unbounded_limit(operator):
    """``limit: 999999999`` used to reach ``qs[:999999999]`` — the whole
    table in one response, from any superuser client."""
    for n in range(205):
        _log(f"op.{n:03d}")

    rows = AuditLogQuery().audit_logs(_info(operator), limit=999999999)
    assert len(rows) == MAX_PAGE_LIMIT


def test_legacy_field_ordering_is_deterministic_now_that_it_truncates(operator):
    """A cap over ``ORDER BY -timestamp`` alone is not reproducible when
    rows share a timestamp: which 200 of 205 you get is up to the plan.
    The tiebreak is what makes the truncation mean something."""
    for n in range(205):
        _log(f"op.{n:03d}")

    rows = AuditLogQuery().audit_logs(_info(operator), limit=999999999)
    assert [r.operation for r in rows] == _db_order()[:MAX_PAGE_LIMIT]


def test_page_field_clamps_an_absurd_limit_too(operator):
    for n in range(205):
        _log(f"op.{n:03d}")

    page = AuditLogQuery().audit_logs_page(_info(operator), limit=999999999)
    assert len(page.items) == MAX_PAGE_LIMIT
    assert page.total_count == 205
    assert page.next_cursor is not None, "205 rows do not fit in one clamped page"


# ---------------------------------------------------------------------------
# The walk.
# ---------------------------------------------------------------------------


def test_walk_reaches_every_row_exactly_once_and_terminates(operator):
    for n in range(205):
        _log(f"op.{n:03d}")

    operations = _walk(AuditLogQuery(), operator, limit=50)
    assert len(operations) == 205
    assert len(set(operations)) == 205, "a row was served twice"
    assert operations == _db_order()


def test_walk_is_newest_first_across_uneven_page_sizes(operator):
    for n in range(7):
        _log(f"op.{n}")

    expected = _db_order()
    assert _walk(AuditLogQuery(), operator, limit=3) == expected
    assert _walk(AuditLogQuery(), operator, limit=1) == expected
    assert _walk(AuditLogQuery(), operator, limit=7) == expected


def test_page_that_lands_exactly_on_the_last_row_still_terminates(operator):
    """``next_cursor`` comes from an overfetched row, not from
    ``len(rows) < limit`` — a walk whose final page is full must still
    end rather than re-serving the tail forever."""
    for n in range(6):
        _log(f"op.{n}")

    page = AuditLogQuery().audit_logs_page(_info(operator), limit=3)
    second = AuditLogQuery().audit_logs_page(_info(operator), limit=3, after=page.next_cursor)
    assert len(second.items) == 3
    assert second.next_cursor is None


def test_garbage_cursor_restarts_from_the_top(operator):
    """A bookmarked URL carrying a stale token is a UX event, not a 400."""
    for n in range(4):
        _log(f"op.{n}")

    page = AuditLogQuery().audit_logs_page(_info(operator), limit=10, after="not-a-real-cursor")
    assert [i.operation for i in page.items] == _db_order()


# ---------------------------------------------------------------------------
# Counting and filtering.
# ---------------------------------------------------------------------------


def test_total_count_is_the_whole_result_set_not_the_page(operator):
    for n in range(12):
        _log(f"op.{n:02d}")

    page = AuditLogQuery().audit_logs_page(_info(operator), limit=5)
    assert len(page.items) == 5
    assert page.total_count == 12


def test_operation_filter_narrows_items_and_count(operator):
    _log("cluster.install_prereqs")
    _log("cluster.teardown")
    _log("app.deploy")

    page = AuditLogQuery().audit_logs_page(_info(operator), operation="cluster.")
    assert sorted(i.operation for i in page.items) == ["cluster.install_prereqs", "cluster.teardown"]
    assert page.total_count == 2


def test_user_id_filter_narrows_items_and_count(operator):
    actor = User.objects.create(username="actor-1235", email="actor-1235@example.test")
    _log("app.deploy", user=actor)
    _log("app.deploy", user=operator)
    _log("app.deploy")

    page = AuditLogQuery().audit_logs_page(_info(operator), user_id=str(actor.pk))
    assert len(page.items) == 1
    assert page.items[0].username == "actor-1235"
    assert page.total_count == 1


def test_search_matches_the_operation_and_narrows_total_count(operator):
    """A count that ignored ``search`` would render "3 results" over a
    one-row table."""
    _log("cluster.install_prereqs")
    _log("app.deploy")
    _log("app.rollback")

    page = AuditLogQuery().audit_logs_page(_info(operator), search="prereqs")
    assert [i.operation for i in page.items] == ["cluster.install_prereqs"]
    assert page.total_count == 1


def test_search_matches_the_acting_user(operator):
    """The operator hunting "what did ana@ do" is the reason ``search``
    reaches through the user FK rather than only the operation column."""
    ana = User.objects.create(username="ana", email="ana@example.test")
    bob = User.objects.create(username="bob", email="bob@example.test")
    _log("app.deploy", user=ana)
    _log("app.deploy", user=bob)
    _log("app.deploy")

    by_username = AuditLogQuery().audit_logs_page(_info(operator), search="ana")
    by_email = AuditLogQuery().audit_logs_page(_info(operator), search="bob@example")

    assert [i.username for i in by_username.items] == ["ana"]
    assert by_username.total_count == 1
    assert [i.username for i in by_email.items] == ["bob"]
    assert by_email.total_count == 1


def test_search_composes_with_the_operation_filter(operator):
    ana = User.objects.create(username="ana-compose", email="ana-compose@example.test")
    _log("cluster.teardown", user=ana)
    _log("app.deploy", user=ana)
    _log("cluster.teardown")

    page = AuditLogQuery().audit_logs_page(_info(operator), operation="cluster.", search="ana-compose")
    assert [i.operation for i in page.items] == ["cluster.teardown"]
    assert page.total_count == 1


def test_page_carries_the_full_entry_payload(operator):
    """The page maps rows through the same ``AuditLogEntry`` builder the
    list field uses; a mapper that dropped a field would ship a table of
    blanks."""
    actor = User.objects.create(username="payload-1235", email="payload-1235@example.test")
    _log("app.deploy", user=actor, success=False)

    entry = AuditLogQuery().audit_logs_page(_info(operator)).items[0]
    assert entry.operation == "app.deploy"
    assert entry.variables == {"op": "app.deploy"}
    assert entry.success is False
    assert entry.username == "payload-1235"
    assert entry.timestamp is not None


# ---------------------------------------------------------------------------
# Access control. This surface is cross-tenant BY DESIGN (#537) — the
# isolation guarantee is that no tenant identity can read it at all,
# not that rows are org-filtered. MutationAuditLog has no organization
# column to filter on.
# ---------------------------------------------------------------------------


def test_anonymous_is_refused(two_tenants):
    from django.contrib.auth.models import AnonymousUser

    _log("app.deploy")
    with pytest.raises(GraphQLError, match="Authentication required"):
        AuditLogQuery().audit_logs_page(_info(AnonymousUser()))


def test_authenticated_tenant_user_is_refused(two_tenants):
    """The whole point of #537: an authed tenant user must not reach the
    cross-tenant log, on the page field any more than the list field."""
    _log("app.deploy")
    with pytest.raises(GraphQLError, match="superuser"):
        AuditLogQuery().audit_logs_page(_info(two_tenants.a.user))


def test_the_gate_runs_before_any_row_is_read(two_tenants):
    """A gate applied after the queryset was built would still leak on a
    resolver that logged or counted first. Nothing is returned at all."""
    _log("app.deploy")
    query = AuditLogQuery()
    with pytest.raises(GraphQLError):
        query.audit_logs_page(_info(two_tenants.a.user), search="deploy")


def test_the_operator_sees_every_tenants_rows(two_tenants, operator):
    """Pins the deliberate exemption: cross-tenant inspection IS the use
    case. If someone later org-scopes this resolver, the install
    operator silently loses the audit trail and this fails."""
    _log("app.deploy", user=two_tenants.a.user)
    _log("app.deploy", user=two_tenants.b.user)

    page = AuditLogQuery().audit_logs_page(_info(operator), limit=50)
    usernames = {i.username for i in page.items}
    assert two_tenants.a.user.username in usernames
    assert two_tenants.b.user.username in usernames
    assert page.total_count == 2
