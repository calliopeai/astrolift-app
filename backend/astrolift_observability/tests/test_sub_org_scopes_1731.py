"""Observability reads accept a sub-org binding on the app they name (#1731).

Every resolver in this module takes an ``app_slug`` and gated on
``APP_READ`` with no ``scope=``, which resolves against the active org
alone. An APP- or TEAM-scoped binding on that very app could never match,
so a ``team_developer`` could deploy an app and then not read its own
logs, metrics, traces or URL health.

The direction of the bug matters for how these read: the unscoped gate is
*stricter* than intended, never looser, so what is asserted here is that
the right people stop being denied -- and that no one new gets in.
"""

from __future__ import annotations

import datetime as dt

import pytest

from astrolift_observability.schema.log_queries import LogHistoryQuery
from astrolift_observability.schema.queries import GoldenSignalsQuery
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import ScopeWorld, as_tenant, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def world():
    return ScopeWorld("obs1731")


@pytest.fixture
def reba():
    return make_user("obs1731")


@pytest.fixture
def info(reba):
    return make_info(reba)


def _signals(info, slug):
    """Reaching the resolver at all is the assertion; with no Prometheus
    endpoint it answers NOT_CONFIGURED rather than raising."""
    return GoldenSignalsQuery().astrolift_app_golden_signals(info, app_slug=slug)


def _logs(info, slug):
    now = dt.datetime.now(tz=dt.UTC)
    return LogHistoryQuery().astrolift_app_logs(
        info, app_slug=slug, since=now - dt.timedelta(hours=1), until=now
    )


def test_app_scoped_binding_reads_its_own_app(world, reba, info):
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="app-reader-1731",
    )
    with as_tenant(world, reba):
        assert _signals(info, world.medops_app.slug) is not None


def test_app_scoped_binding_does_not_read_a_sibling_app(world, reba, info):
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="app-reader-sib-1731",
    )
    with as_tenant(world, reba), pytest.raises(PermissionDenied):
        _signals(info, world.platform_app.slug)


def test_team_scoped_binding_reads_that_teams_app(world, reba, info):
    """The inheritance walk runs downward: TEAM covers the team's apps."""
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="team-reader-1731",
    )
    with as_tenant(world, reba):
        assert _signals(info, world.medops_app.slug) is not None


def test_project_scoped_binding_reads_that_projects_app(world, reba, info):
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="PROJECT",
        scope_id=world.medops_project.id,
        slug="project-reader-1731",
    )
    with as_tenant(world, reba):
        assert _signals(info, world.medops_app.slug) is not None


def test_org_scoped_binding_still_reads_every_app(world, reba, info):
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="ORG",
        scope_id=world.org.id,
        slug="org-reader-1731",
    )
    with as_tenant(world, reba):
        assert _signals(info, world.medops_app.slug) is not None
        assert _signals(info, world.platform_app.slug) is not None


def test_a_binding_in_another_org_reads_nothing(world, reba, info):
    elsewhere = ScopeWorld("obs1731b")
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=elsewhere.medops_app.id,
        slug="foreign-reader-1731",
    )
    with as_tenant(world, reba), pytest.raises(PermissionDenied):
        _signals(info, world.medops_app.slug)


def test_an_unknown_slug_falls_back_to_the_stricter_org_check(world, reba, info):
    """A key that resolves to nothing must never open the door."""
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="unknown-slug-1731",
    )
    with as_tenant(world, reba), pytest.raises(PermissionDenied):
        _signals(info, "no-such-app")


def test_logs_are_scoped_by_their_own_permission(world, reba, info):
    """``astroliftAppLogs`` gates on APP_READ_LOGS, not APP_READ -- the
    scope thread has to follow the permission the resolver declares."""
    bind_role(
        reba,
        permissions=[Permission.APP_READ_LOGS],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="log-reader-1731",
    )
    with as_tenant(world, reba):
        assert _logs(info, world.medops_app.slug) is not None
    with as_tenant(world, reba), pytest.raises(PermissionDenied):
        _logs(info, world.platform_app.slug)
