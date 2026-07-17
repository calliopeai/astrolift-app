"""Tests for the ``registered_app_id`` emit-time backfill (#1111).

The per-app events feed filters on ``Event.registered_app_id``. Emit
sites that stamp only ``resource_kind`` + ``resource_id`` (a slug or a
guid) used to leave the FK ``None``, so those events never surfaced in
the feed even though they scoped to an app. ``Event.emit`` now resolves
the FK from the resource context, best-effort and org-scoped.

Real Postgres — no mocks; we read the persisted row back.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_operations.models import Event
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_registry.models import RegisteredApp
from core.events import Event as EventEmitter
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _tenant(slug: str = "acme") -> tuple[Organization, Team, Project]:
    org = Organization.objects.create(name=slug.title(), slug=slug)
    team = Team.objects.create(name="Eng", slug=f"{slug}-eng", organization=org)
    project = Project.objects.create(name="API", slug=f"{slug}-api", team=team)
    return org, team, project


def _app(*, slug, org, team, project) -> RegisteredApp:
    return RegisteredApp.objects.create(
        organization=org, team=team, project=project, name=slug.title(), slug=slug
    )


def test_emit_backfills_app_fk_from_slug():
    org, team, project = _tenant()
    app = _app(slug="primary", org=org, team=team, project=project)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit(
            "app.exec_pod.opened",
            {"cmd": "sh"},
            resource_kind="app",
            resource_id=app.slug,
        )
    row = Event.objects.get()
    assert row.registered_app_id == app.id


def test_emit_backfills_app_fk_from_guid():
    org, team, project = _tenant()
    app = _app(slug="primary", org=org, team=team, project=project)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit(
            "app.recovered",
            {},
            resource_kind="registered_app",
            resource_id=str(app.guid),
        )
    row = Event.objects.get()
    assert row.registered_app_id == app.id


def test_backfilled_event_appears_in_per_app_feed(permission_resolver):
    """End-to-end: a backfilled event lands on the per-app feed that
    filters on the FK — the whole point of the backfill."""
    org, team, project = _tenant()
    app = _app(slug="primary", org=org, team=team, project=project)
    other = _app(slug="secondary", org=org, team=team, project=project)
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit("app.exec_pod.opened", {}, resource_kind="app", resource_id=app.slug)
        info = type("I", (), {"context": type("C", (), {"user": None})()})()
        rows = OperationsQuery().astrolift_events(info, app_slug=app.slug)
        other_rows = OperationsQuery().astrolift_events(info, app_slug=other.slug)
    assert [r.event_type for r in rows] == ["app.exec_pod.opened"]
    assert other_rows == []


def test_emit_does_not_override_explicit_fk():
    """An explicit ``registered_app_id`` wins over the resource-context
    lookup — the backfill only fills the gap, never overrides."""
    org, team, project = _tenant()
    app = _app(slug="primary", org=org, team=team, project=project)
    other = _app(slug="secondary", org=org, team=team, project=project)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit(
            "app.exec_pod.opened",
            {},
            resource_kind="app",
            resource_id=other.slug,  # resource points at 'other' ...
            registered_app_id=app.id,  # ... but the explicit FK wins.
        )
    row = Event.objects.get()
    assert row.registered_app_id == app.id


def test_emit_no_backfill_for_non_app_resource():
    """A non-app resource kind (e.g. a cluster) whose id happens to
    match an app slug must NOT be linked to the app."""
    org, team, project = _tenant()
    app = _app(slug="primary", org=org, team=team, project=project)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit(
            "cluster.bootstrap_run",
            {},
            resource_kind="cluster",
            resource_id=app.slug,
        )
    row = Event.objects.get()
    assert row.registered_app_id is None


def test_emit_backfill_is_org_scoped():
    """An app slug in org A must not backfill onto an event emitted in
    org B's tenant context — slugs are unique per-org, not global."""
    org_a, team_a, project_a = _tenant("orga")
    org_b, team_b, project_b = _tenant("orgb")
    _app(slug="shared", org=org_a, team=team_a, project=project_a)
    with tenant_context(TenantContext(organization_id=org_b.id)):
        EventEmitter.emit(
            "app.exec_pod.opened",
            {},
            resource_kind="app",
            resource_id="shared",  # only exists in org A
        )
    row = Event.objects.get()
    assert row.registered_app_id is None
