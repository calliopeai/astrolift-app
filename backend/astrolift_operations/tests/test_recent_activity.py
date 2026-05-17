"""Tests for ``astrolift_recent_activity`` — dashboard activity feed (#435)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Team
from astrolift_operations.models import Event
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    """Stand-in for ``strawberry.types.Info`` — the resolvers only
    read ``.context.user``; the ``@tenant_scoped`` guard is satisfied
    by the contextvar."""
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _mktenant() -> tuple[Organization, Team, Project]:
    org = Organization.objects.create(name="ActivityCo", slug="activity-co")
    team = Team.objects.create(name="Platform", slug="platform", organization=org)
    project = Project.objects.create(name="Core", slug="core", team=team)
    return org, team, project


def _mkevent(
    *,
    org: Organization,
    event_type: str,
    seconds_ago: int,
    payload: dict | None = None,
    resource_kind: str = "",
    resource_id: str = "",
    actor_user=None,
    registered_app=None,
) -> Event:
    """Insert one ``Event`` row, dating it ``seconds_ago`` so feeds
    are deterministic across runs.

    ``bulk_create`` bypasses ``auto_now_add`` so we can pin
    ``occurred_at`` directly — the append-only DB trigger refuses
    UPDATE / DELETE but happily accepts INSERT.
    """
    return Event.objects.bulk_create(
        [
            Event(
                event_type=event_type,
                payload=payload or {},
                organization=org,
                occurred_at=timezone.now() - dt.timedelta(seconds=seconds_ago),
                resource_kind=resource_kind,
                resource_id=resource_id,
                actor_user=actor_user,
                registered_app=registered_app,
            )
        ]
    )[0]


# ---------------------------------------------------------------------
# Permission gate
# ---------------------------------------------------------------------


def test_recent_activity_requires_audit_log_read(permission_resolver):
    org, _team, _project = _mktenant()
    # Permission resolver is deny-by-default in tests; no grant here.
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            OperationsQuery().astrolift_recent_activity(_info(), limit=10)


# ---------------------------------------------------------------------
# Lifecycle filter
# ---------------------------------------------------------------------


def test_recent_activity_returns_only_lifecycle_events(permission_resolver):
    org, _team, _project = _mktenant()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        _mkevent(org=org, event_type="deploy.completed", seconds_ago=10)
        _mkevent(org=org, event_type="cluster.bootstrap_run", seconds_ago=20)
        _mkevent(org=org, event_type="secret.rotated", seconds_ago=30)
        # Non-lifecycle: must not appear in the feed.
        _mkevent(org=org, event_type="webhook.test", seconds_ago=5)
        _mkevent(org=org, event_type="AUDIT.permission_check", seconds_ago=1)

        page = OperationsQuery().astrolift_recent_activity(_info(), limit=10)
        types = sorted(i.event_type for i in page.items)
        assert types == [
            "cluster.bootstrap_run",
            "deploy.completed",
            "secret.rotated",
        ]
        assert page.next_cursor is None


# ---------------------------------------------------------------------
# Cursor pagination
# ---------------------------------------------------------------------


def test_recent_activity_pagination_walks_with_no_overlap(permission_resolver):
    org, _team, _project = _mktenant()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        events = [_mkevent(org=org, event_type="deploy.completed", seconds_ago=n) for n in range(1, 8)]
        expected_ids = [str(e.guid) for e in sorted(events, key=lambda e: e.occurred_at, reverse=True)]

        q = OperationsQuery()
        page1 = q.astrolift_recent_activity(_info(), limit=3)
        assert len(page1.items) == 3
        assert page1.next_cursor is not None

        page2 = q.astrolift_recent_activity(_info(), limit=3, cursor=page1.next_cursor)
        assert len(page2.items) == 3
        assert page2.next_cursor is not None

        page3 = q.astrolift_recent_activity(_info(), limit=3, cursor=page2.next_cursor)
        assert len(page3.items) == 1
        assert page3.next_cursor is None

        seen = [str(i.id) for i in page1.items + page2.items + page3.items]
        assert seen == expected_ids, "pagination drifted; rows duplicated or skipped"


def test_recent_activity_garbage_cursor_restarts_at_top(permission_resolver):
    """A bogus cursor should degrade to "start from newest" rather
    than 500. The decoder swallows malformed tokens by design."""
    org, _team, _project = _mktenant()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        for n in range(1, 4):
            _mkevent(org=org, event_type="deploy.completed", seconds_ago=n)
        page = OperationsQuery().astrolift_recent_activity(_info(), limit=10, cursor="!!!not-a-cursor!!!")
        assert len(page.items) == 3


# ---------------------------------------------------------------------
# Row shaping (actor, action, target href)
# ---------------------------------------------------------------------


def test_recent_activity_resolves_actor_display(permission_resolver):
    org, _team, _project = _mktenant()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    User = get_user_model()
    user = User.objects.create_user(
        username="kris", email="kris@example.com", first_name="Kris", last_name="Lee"
    )
    with tenant_context(TenantContext(organization_id=org.id)):
        _mkevent(
            org=org,
            event_type="deploy.completed",
            seconds_ago=1,
            actor_user=user,
        )
        page = OperationsQuery().astrolift_recent_activity(_info(), limit=10)
        assert page.items[0].actor_display == "Kris Lee"


def test_recent_activity_system_actor_when_no_user(permission_resolver):
    org, _team, _project = _mktenant()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        _mkevent(org=org, event_type="cluster.bootstrap_run", seconds_ago=1)
        page = OperationsQuery().astrolift_recent_activity(_info(), limit=10)
        assert page.items[0].actor_display == "system"


def test_recent_activity_deploy_routes_to_app_deployment(permission_resolver):
    org, team, project = _mktenant()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    app = RegisteredApp.objects.create(
        name="Checkout",
        slug="checkout",
        organization=org,
        team=team,
        project=project,
    )
    deploy_guid = "0193abcd-0000-7000-8000-000000000123"
    with tenant_context(TenantContext(organization_id=org.id)):
        _mkevent(
            org=org,
            event_type="deploy.completed",
            seconds_ago=1,
            payload={"deployment_guid": deploy_guid},
            registered_app=app,
        )
        page = OperationsQuery().astrolift_recent_activity(_info(), limit=10)
        item = page.items[0]
        assert item.action == "completed"
        assert item.target_kind == "deploy"
        assert item.target_href == f"/apps/checkout/deployments/{deploy_guid}"
        assert item.target_label.startswith("checkout")


def test_recent_activity_cluster_routes_to_cluster_id(permission_resolver):
    org, _team, _project = _mktenant()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    cluster_id = "01939999-0000-7000-8000-aaaaaaaaaaaa"
    with tenant_context(TenantContext(organization_id=org.id)):
        _mkevent(
            org=org,
            event_type="cluster.bootstrap_run",
            seconds_ago=1,
            resource_kind="TenantCluster",
            resource_id=cluster_id,
            payload={"cluster_name": "prod-eks-1"},
        )
        page = OperationsQuery().astrolift_recent_activity(_info(), limit=10)
        item = page.items[0]
        assert item.target_href == f"/clusters/{cluster_id}"
        assert item.target_label == "prod-eks-1"


def test_recent_activity_secret_routes_to_app_secrets(permission_resolver):
    org, team, project = _mktenant()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    app = RegisteredApp.objects.create(name="API", slug="api", organization=org, team=team, project=project)
    with tenant_context(TenantContext(organization_id=org.id)):
        _mkevent(
            org=org,
            event_type="secret.rotated",
            seconds_ago=1,
            registered_app=app,
        )
        page = OperationsQuery().astrolift_recent_activity(_info(), limit=10)
        assert page.items[0].target_href == "/apps/api/secrets"


def test_recent_activity_unknown_kind_has_null_href(permission_resolver):
    """Events outside the routed prefixes still render — just without
    a clickable target so the row exists as a timeline marker."""
    org, _team, _project = _mktenant()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        _mkevent(
            org=org,
            event_type="drift.detected",
            seconds_ago=1,
            resource_id="anything",
        )
        page = OperationsQuery().astrolift_recent_activity(_info(), limit=10)
        item = page.items[0]
        # No app slug in payload or FK, so no canonical route.
        assert item.target_href is None
        assert item.target_label  # never empty


# ---------------------------------------------------------------------
# Limit clamping
# ---------------------------------------------------------------------


def test_recent_activity_clamps_limit(permission_resolver):
    """Caller-provided limits get clamped to the resolver's window."""
    org, _team, _project = _mktenant()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        for n in range(1, 6):
            _mkevent(org=org, event_type="deploy.completed", seconds_ago=n)
        # negative / zero collapses to 1; oversized clamps to the cap
        page = OperationsQuery().astrolift_recent_activity(_info(), limit=0)
        assert len(page.items) == 1
        page = OperationsQuery().astrolift_recent_activity(_info(), limit=10_000)
        assert len(page.items) == 5
