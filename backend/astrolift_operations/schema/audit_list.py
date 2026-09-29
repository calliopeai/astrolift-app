"""The audit trail on the list contract (spec 44 §5.1, #2151).

One queryset builder for ``astroliftAuditEventsPage`` and
``exportAuditEvents``, so the download is always the rows the operator
is looking at. It takes the legacy flat arguments, the new flat ones
(``search``, ``targetKind``, ``targetId``, ``subjectUserId``) and the
``filter`` input, and ANDs them all.

The trail is a cursor list (it only grows), so ``sort`` takes a single
key, ``occurredAt``, in either direction; see ``README.md`` in
``astrolift_graphql`` for why multi-key sort waits on cursor lists.
"""

from __future__ import annotations

import datetime as dt

import strawberry
from django.contrib.auth import get_user_model
from django.db.models import CharField, Q, QuerySet
from django.db.models.functions import Cast

from astrolift_graphql import FilterField, UnsupportedSort, filter_q, parse_sort_spec, search_q
from astrolift_operations.models import AuditEvent


@strawberry.input(
    name="AstroliftAuditEventsFilter",
    description="The audit trail's declared filters. Unset fields do not filter; list values match any.",
)
class AuditEventsFilterInput:
    actor: list[str] | None = strawberry.field(
        default=None, description='Actor ids (a user\'s pk as a string); "me" is the viewer.'
    )
    action: list[str] | None = strawberry.field(default=None, description="Exact actions, e.g. team.create.")
    decision: list[str] | None = strawberry.field(default=None, description="ALLOW, DENY or UNKNOWN.")
    target_kind: list[str] | None = strawberry.field(
        default=None, description="Target kinds, case-insensitive, e.g. user, role_binding."
    )
    target_id: str | None = strawberry.field(default=None, description="The target's id, exact.")
    subject_user: str | None = strawberry.field(
        default=None,
        description=(
            'Events about one person: a user pk, or "me". Matches events targeting the user, '
            "any of the user's role bindings (granted or revoked), or naming the user in the "
            "event data (bulk revokes, team role assignments)."
        ),
    )
    since: dt.datetime | None = strawberry.field(default=None, description="occurredAt >= since.")
    until: dt.datetime | None = strawberry.field(default=None, description="occurredAt <= until.")


def subject_user_q(user_id: str) -> Q:
    """Events about the user with pk ``user_id``: grants, revokes, anything targeting them.

    A single grant files under the user (``target_kind=user``); a revoke
    files under the binding, which keeps its user after the soft delete;
    bulk revokes and team assignments put ``user_id`` in the data. A value
    that is not a pk matches nothing.
    """
    from astrolift_identity.models import RoleBinding

    try:
        pk = int(user_id)
    except (TypeError, ValueError):
        return Q(pk__in=[])
    bindings = (
        RoleBinding.all_objects.filter(user_id=pk)
        .annotate(guid_text=Cast("guid", CharField()))
        .values("guid_text")
    )
    return (
        Q(target_kind="user", target_id=str(pk))
        | Q(target_kind="role_binding", target_id__in=bindings)
        | Q(data__user_id=pk)
    )


def _any_iexact(field: str):
    def build(values: list[str]) -> Q:
        query = Q()
        for value in values:
            query |= Q(**{f"{field}__iexact": value})
        return query

    return build


_AUDIT_FILTERS = {
    "actor": FilterField("actor_id", me=True),
    "action": FilterField("action"),
    "decision": FilterField(q=lambda values: Q(decision__in=[v.upper() for v in values])),
    "target_kind": FilterField(q=_any_iexact("target_kind")),
    "target_id": FilterField("target_id"),
    "subject_user": FilterField(q=subject_user_q, me=True),
    "since": FilterField("occurred_at__gte"),
    "until": FilterField("occurred_at__lte"),
}


def audit_search_q(term: str) -> Q:
    """The search box: action prefix, actor (display, id, or the user's name), target, request id."""
    User = get_user_model()
    actors = (
        User.objects.filter(Q(username__icontains=term) | Q(email__icontains=term))
        .annotate(pk_text=Cast("pk", CharField()))
        .values("pk_text")
    )
    return search_q(
        term, "actor_display", "target_slug", prefix=("action", "actor_id", "target_id", "request_id")
    ) | Q(actor_kind="user", actor_id__in=actors)


def audit_events_qs(
    org_id: int,
    *,
    action: str | None = None,
    decision: str | None = None,
    actor_id: str | None = None,
    created_at_gte: dt.datetime | None = None,
    created_at_lte: dt.datetime | None = None,
    search: str | None = None,
    target_kind: str | None = None,
    target_id: str | None = None,
    subject_user_id: str | None = None,
    filter: AuditEventsFilterInput | None = None,
    viewer_id: int | None = None,
) -> QuerySet[AuditEvent]:
    """The caller org's audit rows matching every argument that was set. Unordered."""
    qs = AuditEvent.objects.filter(organization_id=org_id)
    if action:
        qs = qs.filter(action=action)
    if decision:
        qs = qs.filter(decision=decision.upper())
    if actor_id:
        qs = qs.filter(actor_id=actor_id)
    if created_at_gte is not None:
        qs = qs.filter(occurred_at__gte=created_at_gte)
    if created_at_lte is not None:
        qs = qs.filter(occurred_at__lte=created_at_lte)
    if target_kind:
        qs = qs.filter(target_kind__iexact=target_kind)
    if target_id:
        qs = qs.filter(target_id=target_id)
    if subject_user_id:
        qs = qs.filter(subject_user_q(subject_user_id))
    if search and search.strip():
        qs = qs.filter(audit_search_q(search.strip()))
    me = str(viewer_id) if viewer_id is not None else None
    return qs.filter(filter_q(filter, _AUDIT_FILTERS, me=me))


def audit_sort(sort: str | None) -> tuple[bool, str]:
    """``(descending, cursor_scope)`` for ``sort``; newest first by default.

    Refuses any key but ``occurredAt``, and more than one key. Newest
    first keeps the unscoped cursor the page has always issued, so a
    cursor minted before ``sort`` existed still continues its walk;
    oldest first is scoped, so a cursor from the other order restarts.
    """
    pairs = parse_sort_spec(sort) or [("occurredAt", True)]
    if len(pairs) > 1 or pairs[0][0] != "occurredAt":
        raise UnsupportedSort(f"sort {sort!r} is not available on the audit trail; supported: occurredAt")
    descending = pairs[0][1]
    return descending, "" if descending else "audit:occurredAt"
