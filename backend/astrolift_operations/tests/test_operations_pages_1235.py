"""Cursor pagination for the operations list surfaces (#1235).

Every list resolver in this app shipped with a hard row cap and no way
past it: 500 events, 200 alert rules, 500 alert firings, 200 webhook
subscriptions, 100 delivery attempts. For the surfaces that matter
during an incident that cap IS the bug — a hook retrying on every event
burns 100 delivery rows in minutes, so "when did this integration start
failing?" was unanswerable from the UI.

These tests pin the four properties that make the replacement honest:
the walk reaches every row exactly once and terminates, ``totalCount``
describes the whole filtered set rather than the visible page, ``search``
narrows both, and no page (nor count) ever shows a sibling org's rows.

Real Postgres, no DB mocks. Rows written in a tight loop can share a
timestamp — that exercises the ``guid`` tiebreak — so ordering is always
asserted against the database's own ``ORDER BY``, never against creation
order.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Team
from astrolift_operations.models import (
    AlertEvent,
    AlertRule,
    Event,
    WebhookDelivery,
    WebhookSubscription,
)
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.schema.enums import ObservabilityPanelReason
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

# Every walk below is bounded so a non-terminating cursor fails loudly
# with "walk did not terminate" instead of hanging the suite.
_MAX_PAGES = 40


def _info():
    """Stand-in for ``strawberry.types.Info``: these resolvers read
    nothing off it, and ``@tenant_scoped`` is satisfied by the
    contextvar the ``tenant_context`` manager sets."""
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _org(slug: str) -> Organization:
    return Organization.objects.create(name=f"Org {slug}", slug=slug)


def _app(org: Organization, slug: str) -> RegisteredApp:
    team = Team.objects.create(organization=org, name=f"T {slug}", slug=f"team-{slug}")
    project = Project.objects.create(team=team, name=f"P {slug}", slug=f"proj-{slug}")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=f"App {slug}",
        slug=slug,
    )


def _events(
    org: Organization,
    specs: list[tuple[str, int]],
    *,
    resource_kind: str = "workload",
    resource_id: str = "web",
    registered_app: RegisteredApp | None = None,
) -> list[Event]:
    """Insert ``(event_type, seconds_ago)`` rows.

    ``occurred_at`` is ``auto_now_add``, and **bulk_create does not bypass
    that** — Django calls ``DateTimeField.pre_save`` per instance there too,
    which overwrites the value with ``timezone.now()``. The rows therefore
    landed with timestamps microseconds apart in list order, so a newest-first
    read returned the list exactly reversed rather than in ``seconds_ago``
    order. An interleaved ``a b a b a b`` stream came back ``b a b a b a``.

    ``QuerySet.update`` is the operation that really does skip ``pre_save``,
    so the intended timestamps are written after the insert.
    """
    now = timezone.now()
    rows = Event.objects.bulk_create(
        [
            Event(
                organization=org,
                event_type=event_type,
                payload={},
                resource_kind=resource_kind,
                resource_id=resource_id,
                registered_app=registered_app,
            )
            for event_type, _seconds_ago in specs
        ]
    )
    for row, (_event_type, seconds_ago) in zip(rows, specs, strict=True):
        occurred_at = now - dt.timedelta(seconds=seconds_ago)
        Event.objects.filter(pk=row.pk).update(occurred_at=occurred_at)
        row.occurred_at = occurred_at
    return rows


def _rule(org: Organization, name: str, **kwargs) -> AlertRule:
    return AlertRule.objects.create(
        organization=org,
        name=name,
        target=kwargs.pop("target", AlertRule.Target.APP),
        target_id=kwargs.pop("target_id", ""),
        predicate={},
        notify_channels=[],
        **kwargs,
    )


def _firing(rule: AlertRule, *, seconds_ago: int, summary: str = "", **kwargs) -> AlertEvent:
    return AlertEvent.objects.create(
        rule=rule,
        organization=rule.organization,
        fired_at=timezone.now() - dt.timedelta(seconds=seconds_ago),
        summary=summary,
        detail={},
        **kwargs,
    )


def _subscription(org: Organization, url: str, **kwargs) -> WebhookSubscription:
    return WebhookSubscription.objects.create(
        organization=org,
        url=url,
        secret_hash="deadbeef",
        events=["deploy.completed"],
        is_active=True,
        **kwargs,
    )


def _delivery(sub: WebhookSubscription, *, event_type: str, seconds_ago: int, **kwargs) -> WebhookDelivery:
    return WebhookDelivery.objects.create(
        subscription=sub,
        event_type=event_type,
        delivered_at=timezone.now() - dt.timedelta(seconds=seconds_ago),
        status_code=kwargs.pop("status_code", 200),
        latency_ms=kwargs.pop("latency_ms", 12),
        success=kwargs.pop("success", True),
        **kwargs,
    )


def _walk(fetch, project) -> list:
    """Page a resolver to exhaustion, returning the projected rows.

    ``fetch(cursor)`` returns one page; ``project(item)`` pulls the
    field the caller asserts on.
    """
    seen: list = []
    cursor: str | None = None
    for _ in range(_MAX_PAGES):
        page = fetch(cursor)
        seen.extend(project(item) for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return seen
    raise AssertionError("walk did not terminate")


# ---------------------------------------------------------------------------
# astroliftEventsPage — the target astroliftEvents now defers to
# ---------------------------------------------------------------------------


def test_events_page_walk_reaches_every_row_past_the_list_cap(permission_resolver):
    """The regression the deprecation is about: ``astroliftEvents``
    hard-caps at 500 rows, so on a busy org event 501 does not exist as
    far as the UI is concerned — not merely "slow to reach"."""
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    org = _org("ev-walk")
    _events(org, [("deploy.completed", n) for n in range(505)])
    expected = [
        str(g)
        for g in Event.objects.filter(organization=org)
        .order_by("-occurred_at", "-guid")
        .values_list("guid", flat=True)
    ]

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        capped = q.astrolift_events(_info(), limit=10_000)
        assert len(capped) == 500, "precondition: the list field still caps"

        seen = _walk(
            lambda cursor: q.astrolift_events_page(_info(), limit=100, after=cursor),
            lambda item: str(item.id),
        )
    assert seen == expected, "pagination drifted; rows duplicated or skipped"
    assert len(set(seen)) == 505, "a row was served twice"


def test_events_page_search_narrows_the_stream(permission_resolver):
    """Free-text search reaches event type, resource identity, and the
    owning app's slug — what an operator actually types into the /events
    filter box."""
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    org = _org("ev-search")
    app = _app(org, "checkout")
    _events(org, [("deploy.completed", 1)], registered_app=app)
    _events(org, [("secret.rotated", 2)], resource_kind="secret", resource_id="db-password")
    _events(org, [("cluster.bootstrap_run", 3)], resource_kind="cluster", resource_id="prod-eks")

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_type = q.astrolift_events_page(_info(), search="secret.rot")
        by_resource = q.astrolift_events_page(_info(), search="db-password")
        by_app_slug = q.astrolift_events_page(_info(), search="checkout")
        unmatched = q.astrolift_events_page(_info(), search="nothing-matches-this")

    assert [i.event_type for i in by_type.items] == ["secret.rotated"]
    assert [i.event_type for i in by_resource.items] == ["secret.rotated"]
    assert [i.event_type for i in by_app_slug.items] == ["deploy.completed"]
    assert unmatched.items == []
    assert unmatched.reason == ObservabilityPanelReason.NO_DATA_YET


def test_events_page_hides_other_orgs_rows(permission_resolver):
    """Event carries its own organization FK, but the manager is not
    tenant-aware — the org clause is the resolver's job (#1183)."""
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    ours = _org("ev-ours")
    theirs = _org("ev-theirs")
    _events(ours, [("deploy.completed", 1)])
    _events(theirs, [("deploy.completed", 2), ("deploy.completed", 3)])

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=ours.id)):
        seen = _walk(
            lambda cursor: q.astrolift_events_page(_info(), limit=5, after=cursor),
            lambda item: str(item.organization_id),
        )
    assert seen == [str(ours.id)]


def test_events_page_limit_zero_still_yields_one_row(permission_resolver):
    """This surface clamps ``limit`` with ``max(1, min(limit, 500))``,
    not the shared helper's "fall back to the default page size" rule.
    Moving onto ``keyset_page`` must not quietly turn ``limit: 0`` into
    a 50-row page."""
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    org = _org("ev-clamp")
    _events(org, [("deploy.completed", n) for n in range(5)])

    with tenant_context(TenantContext(organization_id=org.id)):
        page = OperationsQuery().astrolift_events_page(_info(), limit=0)
    assert len(page.items) == 1


def test_events_page_reason_separates_empty_stream_from_exhausted_walk(permission_resolver):
    """``reason`` is what the per-app panel renders "no activity yet"
    from; a continuation that ran off the end is NOT that."""
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    org = _org("ev-reason")

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        empty = q.astrolift_events_page(_info(), limit=5)
        assert empty.reason == ObservabilityPanelReason.NO_DATA_YET

        _events(org, [("deploy.completed", n) for n in range(2)])
        first = q.astrolift_events_page(_info(), limit=1)
        assert first.reason == ObservabilityPanelReason.OK

        exhausted = q.astrolift_events_page(_info(), limit=5, after=first.next_cursor)
        assert exhausted.reason == ObservabilityPanelReason.OK


def test_deprecated_events_list_and_page_agree_on_membership(permission_resolver):
    """Both read one queryset builder, so a filter added to one can
    never silently skip the other."""
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    org = _org("ev-agree")
    _events(org, [("deploy.completed", 1), ("deploy.failed", 2), ("deploy.completed", 3)])

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        listed = q.astrolift_events(_info(), limit=100, event_type="deploy.completed")
        paged = _walk(
            lambda cursor: q.astrolift_events_page(
                _info(), limit=1, after=cursor, event_type="deploy.completed"
            ),
            lambda item: str(item.id),
        )
    assert [str(i.id) for i in listed] == paged


# ---------------------------------------------------------------------------
# astroliftEventsAggregatedPage
# ---------------------------------------------------------------------------


def test_aggregated_page_counts_every_raw_event_exactly_once(permission_resolver):
    """Buckets are folds, not rows, so the guarantee is stated over the
    raw stream: across the whole walk the bucket counts sum to the
    number of matching events, no more and no less.

    The stream is interleaved on purpose (A B A B A B) so a one-bucket
    page has to cut mid-fold — the case where a naive implementation
    either drops the tail or re-serves it.
    """
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    org = _org("agg-walk")
    specs: list[tuple[str, int]] = []
    for n in range(6):
        specs.append(("a.fired" if n % 2 == 0 else "b.fired", n))
    _events(org, specs)

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        counts = _walk(
            lambda cursor: q.astrolift_events_aggregated_page(_info(), limit=1, after=cursor),
            lambda bucket: (bucket.event_type, bucket.count),
        )

    assert sum(count for _kind, count in counts) == 6, "a raw event was dropped or double-counted"
    # limit=1 with two interleaved keys forces a cut on every page.
    assert [kind for kind, _count in counts] == [
        "a.fired",
        "b.fired",
        "a.fired",
        "b.fired",
        "a.fired",
        "b.fired",
    ]


def test_aggregated_page_folds_a_burst_into_one_bucket(permission_resolver):
    """The whole point of the surface: identical consecutive events
    inside the window collapse to one row carrying the count."""
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    org = _org("agg-fold")
    _events(org, [("workload.unhealthy", n) for n in range(8)])

    with tenant_context(TenantContext(organization_id=org.id)):
        page = OperationsQuery().astrolift_events_aggregated_page(
            _info(), limit=10, aggregate_window_seconds=300
        )
    assert len(page.items) == 1
    assert page.items[0].count == 8
    assert page.next_cursor is None


def test_aggregated_page_total_count_is_the_whole_raw_result_set(permission_resolver):
    """``totalCount`` reports matching RAW events, not buckets, and does
    not shrink as the walk advances."""
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    org = _org("agg-total")
    _events(org, [("a.fired" if n % 2 == 0 else "b.fired", n) for n in range(6)])

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        first = q.astrolift_events_aggregated_page(_info(), limit=1)
        second = q.astrolift_events_aggregated_page(_info(), limit=1, after=first.next_cursor)

    assert first.total_count == 6
    assert second.total_count == 6, "count must not shrink as the walk advances"


def test_aggregated_page_search_narrows_total_count_not_just_the_page(permission_resolver):
    """A count that ignored the search would render "6 events" over a
    two-event rollup."""
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    org = _org("agg-search")
    _events(org, [("workload.unhealthy", n) for n in range(2)], resource_id="keep-me")
    _events(org, [("deploy.completed", n) for n in range(4)], resource_id="other")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = OperationsQuery().astrolift_events_aggregated_page(_info(), search="keep-me")

    assert page.total_count == 2
    assert [b.event_type for b in page.items] == ["workload.unhealthy"]
    assert page.items[0].count == 2


def test_aggregated_page_hides_other_orgs_rows(permission_resolver):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    ours = _org("agg-ours")
    theirs = _org("agg-theirs")
    _events(ours, [("a.fired", 1)])
    _events(theirs, [("b.fired", 2), ("b.fired", 3)])

    with tenant_context(TenantContext(organization_id=ours.id)):
        page = OperationsQuery().astrolift_events_aggregated_page(_info(), limit=10)

    assert [b.event_type for b in page.items] == ["a.fired"]
    assert page.total_count == 1, "the count leaked the other org's rows"


# ---------------------------------------------------------------------------
# astroliftAlertRulesPage
# ---------------------------------------------------------------------------


def test_alert_rules_page_walk_covers_every_rule_once(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org = _org("rules-walk")
    for n in range(9):
        _rule(org, f"rule-{n:02d}")
    expected = list(
        AlertRule.objects.filter(organization=org)
        .order_by("-created_at", "-guid")
        .values_list("name", flat=True)
    )

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        seen = _walk(
            lambda cursor: q.astrolift_alert_rules_page(_info(), limit=4, after=cursor),
            lambda item: item.name,
        )
    assert seen == expected
    assert len(set(seen)) == 9, "a rule was served twice"


def test_alert_rules_page_total_count_spans_the_filtered_set(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org = _org("rules-total")
    for n in range(7):
        _rule(org, f"rule-{n}")
    _rule(org, "retired", is_active=False)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = OperationsQuery().astrolift_alert_rules_page(_info(), limit=3)

    assert len(page.items) == 3
    # active_only defaults True, so the retired rule is out of both the
    # page and the count.
    assert page.total_count == 7


def test_alert_rules_page_search_narrows_total_count(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org = _org("rules-search")
    _rule(org, "5xx rate on checkout", target_id="checkout")
    _rule(org, "latency p99", target_id="billing")
    _rule(org, "oom killed", target_id="billing")

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_name = q.astrolift_alert_rules_page(_info(), search="latency")
        by_target = q.astrolift_alert_rules_page(_info(), search="billing")

    assert [r.name for r in by_name.items] == ["latency p99"]
    assert by_name.total_count == 1
    assert by_target.total_count == 2


def test_alert_rules_page_hides_other_orgs_rules(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    ours = _org("rules-ours")
    theirs = _org("rules-theirs")
    _rule(ours, "ours")
    _rule(theirs, "theirs-a")
    _rule(theirs, "theirs-b")

    with tenant_context(TenantContext(organization_id=ours.id)):
        page = OperationsQuery().astrolift_alert_rules_page(_info(), limit=50)

    assert [r.name for r in page.items] == ["ours"]
    assert page.total_count == 1, "the count leaked the other org's rules"


def test_deprecated_alert_rules_list_and_page_agree(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org = _org("rules-agree")
    _rule(org, "active-a")
    _rule(org, "active-b")
    _rule(org, "muted-target", target=AlertRule.Target.GLOBAL)
    _rule(org, "retired", is_active=False)

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        listed = q.astrolift_alert_rules(_info())
        paged = _walk(
            lambda cursor: q.astrolift_alert_rules_page(_info(), limit=2, after=cursor),
            lambda item: item.name,
        )
    # Membership, not sequence: rules created in the same microsecond
    # tie on ``created_at``, and the list field's ORDER BY has no
    # tiebreak to settle it. What must hold is that the retired rule is
    # absent from both and no rule shows up in only one.
    assert len(paged) == len(listed)
    assert {r.name for r in listed} == set(paged) == {"active-a", "active-b", "muted-target"}


# ---------------------------------------------------------------------------
# astroliftAlertEventsPage
# ---------------------------------------------------------------------------


def test_alert_events_page_walk_seeks_on_fired_at(permission_resolver):
    """``fired_at`` is the seek key, not ``created_at``: it is the
    moment the predicate matched, and unlike ``resolved_at`` it is NOT
    NULL, so the walk can't dead-end on a still-firing alert."""
    permission_resolver.grant(Permission.APP_READ)
    org = _org("fire-walk")
    rule = _rule(org, "flapping")
    for n in range(8):
        _firing(rule, seconds_ago=n, summary=f"fire-{n}")
    expected = list(
        AlertEvent.objects.filter(rule=rule).order_by("-fired_at", "-guid").values_list("summary", flat=True)
    )

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        seen = _walk(
            lambda cursor: q.astrolift_alert_events_page(_info(), limit=3, after=cursor),
            lambda item: item.summary,
        )
    assert seen == expected
    assert len(set(seen)) == 8, "a firing was served twice"


def test_alert_events_page_unresolved_filter_applies_to_page_and_count(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org = _org("fire-unresolved")
    rule = _rule(org, "noisy")
    for n in range(3):
        _firing(rule, seconds_ago=n, summary=f"open-{n}")
    for n in range(3, 6):
        _firing(rule, seconds_ago=n, summary=f"closed-{n}", resolved_at=timezone.now())

    with tenant_context(TenantContext(organization_id=org.id)):
        page = OperationsQuery().astrolift_alert_events_page(_info(), unresolved_only=True, limit=50)

    assert sorted(e.summary for e in page.items) == ["open-0", "open-1", "open-2"]
    assert page.total_count == 3


def test_alert_events_page_search_narrows_total_count(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org = _org("fire-search")
    checkout = _rule(org, "checkout 5xx")
    billing = _rule(org, "billing latency")
    _firing(checkout, seconds_ago=1, summary="error budget burned")
    _firing(billing, seconds_ago=2, summary="p99 above 2s")
    _firing(billing, seconds_ago=3, summary="p99 above 3s")

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_summary = q.astrolift_alert_events_page(_info(), search="error budget")
        by_rule_name = q.astrolift_alert_events_page(_info(), search="billing")

    assert [e.summary for e in by_summary.items] == ["error budget burned"]
    assert by_summary.total_count == 1
    assert by_rule_name.total_count == 2


def test_alert_events_page_hides_other_orgs_firings(permission_resolver):
    """AlertEvent reaches the tenant through its rule; the join filter
    is the whole boundary (#1183)."""
    permission_resolver.grant(Permission.APP_READ)
    ours = _org("fire-ours")
    theirs = _org("fire-theirs")
    _firing(_rule(ours, "ours"), seconds_ago=1, summary="ours")
    their_rule = _rule(theirs, "theirs")
    _firing(their_rule, seconds_ago=2, summary="theirs-a")
    _firing(their_rule, seconds_ago=3, summary="theirs-b")

    with tenant_context(TenantContext(organization_id=ours.id)):
        page = OperationsQuery().astrolift_alert_events_page(_info(), limit=50)

    assert [e.summary for e in page.items] == ["ours"]
    assert page.total_count == 1, "the count leaked the other org's firings"


# ---------------------------------------------------------------------------
# astroliftWebhookSubscriptionsPage
# ---------------------------------------------------------------------------


def test_webhook_subscriptions_page_walk_covers_every_hook_once(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    org = _org("hooks-walk")
    for n in range(7):
        _subscription(org, f"https://example.invalid/hook-{n}")
    expected = list(
        WebhookSubscription.objects.filter(organization=org)
        .order_by("-created_at", "-guid")
        .values_list("url", flat=True)
    )

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        seen = _walk(
            lambda cursor: q.astrolift_webhook_subscriptions_page(_info(), limit=2, after=cursor),
            lambda item: item.url,
        )
    assert seen == expected
    assert len(set(seen)) == 7, "a subscription was served twice"


def test_webhook_subscriptions_page_keeps_the_org_wide_vs_app_split(permission_resolver):
    """Without ``appSlug`` the surface lists org-wide hooks only; the
    app-scoped ones belong to the app's own page (#281). Both branches
    ride the shared queryset builder, so neither can drift."""
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    org = _org("hooks-split")
    app = _app(org, "payments")
    _subscription(org, "https://example.invalid/org-wide")
    _subscription(org, "https://example.invalid/app-bound", registered_app=app)

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        org_wide = q.astrolift_webhook_subscriptions_page(_info(), limit=50)
        app_scoped = q.astrolift_webhook_subscriptions_page(_info(), app_slug="payments", limit=50)

    assert [s.url for s in org_wide.items] == ["https://example.invalid/org-wide"]
    assert org_wide.total_count == 1
    assert [s.url for s in app_scoped.items] == ["https://example.invalid/app-bound"]
    assert app_scoped.total_count == 1


def test_webhook_subscriptions_page_search_narrows_total_count(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    org = _org("hooks-search")
    _subscription(org, "https://hooks.slack.example/services/T1")
    _subscription(org, "https://pagerduty.example/integration/abc")
    _subscription(org, "https://pagerduty.example/integration/def")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = OperationsQuery().astrolift_webhook_subscriptions_page(_info(), search="slack")

    assert [s.url for s in page.items] == ["https://hooks.slack.example/services/T1"]
    assert page.total_count == 1


def test_webhook_subscriptions_page_hides_other_orgs_hooks(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    ours = _org("hooks-ours")
    theirs = _org("hooks-theirs")
    _subscription(ours, "https://example.invalid/ours")
    _subscription(theirs, "https://example.invalid/theirs-a")
    _subscription(theirs, "https://example.invalid/theirs-b")

    with tenant_context(TenantContext(organization_id=ours.id)):
        page = OperationsQuery().astrolift_webhook_subscriptions_page(_info(), limit=50)

    assert [s.url for s in page.items] == ["https://example.invalid/ours"]
    assert page.total_count == 1, "the count leaked the other org's hooks"


# ---------------------------------------------------------------------------
# astroliftWebhookDeliveriesPage
# ---------------------------------------------------------------------------


def test_webhook_deliveries_page_walk_reaches_past_the_old_hundred_row_cap(permission_resolver):
    """The regression that motivated converting this one: a retrying
    hook burns 100 attempts in minutes, and attempt 101 did not exist
    as far as the UI was concerned."""
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    org = _org("deliv-walk")
    sub = _subscription(org, "https://example.invalid/hook")
    for n in range(105):
        _delivery(sub, event_type=f"deploy.evt.{n:03d}", seconds_ago=n)

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        capped = q.astrolift_webhook_deliveries(_info(), subscription_id=str(sub.guid), limit=1000)
        assert len(capped) == 100, "precondition: the list field still caps"

        seen = _walk(
            lambda cursor: q.astrolift_webhook_deliveries_page(
                _info(), subscription_id=str(sub.guid), limit=50, after=cursor
            ),
            lambda item: item.event_type,
        )

    expected = list(
        WebhookDelivery.objects.filter(subscription=sub)
        .order_by("-delivered_at", "-guid")
        .values_list("event_type", flat=True)
    )
    assert seen == expected
    assert len(set(seen)) == 105, "an attempt was served twice"


def test_webhook_deliveries_page_scopes_to_the_named_subscription(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    org = _org("deliv-scope")
    mine = _subscription(org, "https://example.invalid/mine")
    sibling = _subscription(org, "https://example.invalid/sibling")
    _delivery(mine, event_type="deploy.completed", seconds_ago=1)
    _delivery(sibling, event_type="deploy.failed", seconds_ago=2)
    _delivery(sibling, event_type="deploy.failed", seconds_ago=3)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = OperationsQuery().astrolift_webhook_deliveries_page(
            _info(), subscription_id=str(mine.guid), limit=50
        )

    assert [d.event_type for d in page.items] == ["deploy.completed"]
    assert page.total_count == 1


def test_webhook_deliveries_page_search_narrows_total_count(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    org = _org("deliv-search")
    sub = _subscription(org, "https://example.invalid/hook")
    _delivery(sub, event_type="deploy.completed", seconds_ago=1, delivery_id="d-abc")
    _delivery(sub, event_type="deploy.failed", seconds_ago=2, success=False, error="upstream timed out")
    _delivery(sub, event_type="deploy.failed", seconds_ago=3, success=False, error="connection refused")

    q = OperationsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_event = q.astrolift_webhook_deliveries_page(
            _info(), subscription_id=str(sub.guid), search="completed"
        )
        by_error = q.astrolift_webhook_deliveries_page(
            _info(), subscription_id=str(sub.guid), search="timed out"
        )
        by_delivery_id = q.astrolift_webhook_deliveries_page(
            _info(), subscription_id=str(sub.guid), search="d-abc"
        )

    assert by_event.total_count == 1
    assert [d.event_type for d in by_error.items] == ["deploy.failed"]
    assert by_error.total_count == 1
    assert by_delivery_id.total_count == 1


def test_webhook_deliveries_page_refuses_another_orgs_subscription(permission_resolver):
    """A sibling-org subscription guid must read as "no deliveries" in
    the page AND the count — guids are globally unique, so the org
    clause on the subscription lookup is the whole boundary (#1183)."""
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    ours = _org("deliv-ours")
    theirs = _org("deliv-theirs")
    their_sub = _subscription(theirs, "https://example.invalid/theirs")
    _delivery(their_sub, event_type="deploy.completed", seconds_ago=1)
    _delivery(their_sub, event_type="deploy.completed", seconds_ago=2)

    with tenant_context(TenantContext(organization_id=ours.id)):
        page = OperationsQuery().astrolift_webhook_deliveries_page(
            _info(), subscription_id=str(their_sub.guid), limit=50
        )

    assert page.items == []
    assert page.total_count == 0, "the count leaked another org's delivery history"
    assert page.next_cursor is None


def test_webhook_deliveries_page_unknown_subscription_is_empty(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    org = _org("deliv-unknown")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = OperationsQuery().astrolift_webhook_deliveries_page(
            _info(), subscription_id="0193abcd-0000-7000-8000-000000000123", limit=50
        )

    assert page.items == []
    assert page.total_count == 0
