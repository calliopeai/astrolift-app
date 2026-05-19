"""Tests for the ``app_slug`` filter on event queries (#539).

The mobile + web per-app event tabs previously had to fetch a wide
page and narrow client-side via ``event.resourceKind`` /
``payload.appSlug`` — pagination was uneven because a page may
contain zero matches after the client filter. This commit moves the
filter server-side via the ``registered_app`` FK that the emit sites
already populate when the event scopes to an app.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_operations.models import Event
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_registry.models import RegisteredApp
from core.events import Event as EventEmitter
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _make_tenant(slug: str = "acme") -> tuple[Organization, Team, Project]:
    org = Organization.objects.create(name=slug.title(), slug=slug)
    team = Team.objects.create(name="Eng", slug=f"{slug}-eng", organization=org)
    project = Project.objects.create(name="API", slug=f"{slug}-api", team=team)
    return org, team, project


def _make_app(*, slug: str, org: Organization, team: Team, project: Project) -> RegisteredApp:
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=slug.title(),
        slug=slug,
    )


def _make_tenant_with_apps() -> tuple[Organization, RegisteredApp, RegisteredApp]:
    org, team, project = _make_tenant("acme")
    primary = _make_app(slug="primary", org=org, team=team, project=project)
    secondary = _make_app(slug="secondary", org=org, team=team, project=project)
    return org, primary, secondary


def test_astrolift_events_filters_by_app_slug(permission_resolver):
    org, primary, secondary = _make_tenant_with_apps()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit("deploy.completed", {}, registered_app_id=primary.id)
        EventEmitter.emit("config.synced", {}, registered_app_id=secondary.id)
        EventEmitter.emit("cluster.bootstrap_run", {})  # no app

        rows = OperationsQuery().astrolift_events(_info(), app_slug="primary")
    assert len(rows) == 1
    assert rows[0].event_type == "deploy.completed"


def test_astrolift_events_page_filters_by_app_slug(permission_resolver):
    org, primary, secondary = _make_tenant_with_apps()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit("deploy.completed", {}, registered_app_id=primary.id)
        EventEmitter.emit("deploy.failed", {}, registered_app_id=secondary.id)

        page = OperationsQuery().astrolift_events_page(_info(), app_slug="secondary")
    assert len(page.items) == 1
    assert page.items[0].event_type == "deploy.failed"


def test_astrolift_events_page_combines_app_slug_and_severity(permission_resolver):
    """Filters compose: ``app_slug=primary`` + ``severity=error`` returns
    only the intersection (#539 + #540)."""
    org, primary, _secondary = _make_tenant_with_apps()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit("deploy.completed", {}, registered_app_id=primary.id)
        EventEmitter.emit("deploy.failed", {}, registered_app_id=primary.id)
        EventEmitter.emit("config.synced", {}, registered_app_id=primary.id)

        page = OperationsQuery().astrolift_events_page(
            _info(),
            app_slug="primary",
            severity="error",
        )
    assert len(page.items) == 1
    assert page.items[0].event_type == "deploy.failed"
    assert page.items[0].severity == "error"


def test_app_slug_filter_does_not_leak_across_organizations(permission_resolver):
    """A sibling org's events with the same slug must not leak through —
    tenancy is enforced by the existing ``@tenant_scoped`` decorator on
    the resolver, but explicit coverage here so a regression that drops
    the decorator is caught."""
    org_a, team_a, project_a = _make_tenant("orga")
    org_b, team_b, project_b = _make_tenant("orgb")
    # ``RegisteredApp.slug`` is unique-per-organization (not global),
    # so the same slug across two orgs is the realistic shape of a
    # cross-tenant leak.
    app_a = _make_app(slug="shared", org=org_a, team=team_a, project=project_a)
    app_b = _make_app(slug="shared", org=org_b, team=team_b, project=project_b)

    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org_a.id)):
        EventEmitter.emit("deploy.completed", {}, registered_app_id=app_a.id)
    with tenant_context(TenantContext(organization_id=org_b.id)):
        EventEmitter.emit("deploy.failed", {}, registered_app_id=app_b.id)

    # Caller is in org A; querying ``app_slug='shared'`` must return
    # only org A's event.
    with tenant_context(TenantContext(organization_id=org_a.id)):
        page = OperationsQuery().astrolift_events_page(_info(), app_slug="shared")

    assert len(page.items) == 1
    assert page.items[0].event_type == "deploy.completed"
    # The org B event exists in the DB but was filtered by tenancy.
    assert Event.objects.count() == 2


def test_app_slug_unset_returns_all_events(permission_resolver):
    org, primary, _secondary = _make_tenant_with_apps()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit("deploy.completed", {}, registered_app_id=primary.id)
        EventEmitter.emit("cluster.bootstrap_run", {})  # no app

        page = OperationsQuery().astrolift_events_page(_info())
    assert len(page.items) == 2
