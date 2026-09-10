"""Lifecycle gates accept a sub-org binding on the app they act on (#1731).

This is the module where the gap bit hardest: a ``team_developer`` is
defined by being able to deploy, and every deploy, rollback, environment
and domain gate resolved its check against the active org alone, so an
APP- or TEAM-scoped binding could never match its own app.

Most of these name their target indirectly -- a deployment guid, an
environment guid, a custom-domain guid -- so what is asserted here is
that the walk from the named row to its owning app runs, stays inside the
caller's org, and returns nothing (leaving the stricter org check
standing) when the key resolves to nothing.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.schema.queries import LifecycleQuery
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import (
    ScopeWorld,
    as_tenant,
    bind_role,
    make_cluster,
    make_info,
    make_user,
)

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
    w = ScopeWorld("lc1731")
    cluster = make_cluster(w, "lc1731")
    w.medops_env = AppEnvironment.objects.create(
        registered_app=w.medops_app,
        tenant_cluster=cluster,
        name="prod",
        url="https://qs-ops.example.com",
    )
    w.platform_env = AppEnvironment.objects.create(
        registered_app=w.platform_app,
        tenant_cluster=cluster,
        name="prod",
        url="https://gateway.example.com",
    )
    w.medops_deployment = Deployment.objects.create(
        registered_app=w.medops_app, app_environment=w.medops_env, image_tag="v1"
    )
    w.platform_deployment = Deployment.objects.create(
        registered_app=w.platform_app, app_environment=w.platform_env, image_tag="v1"
    )
    return w


@pytest.fixture
def reba():
    return make_user("lc1731")


@pytest.fixture
def info(reba):
    return make_info(reba)


def _deployments(info, slug):
    return LifecycleQuery().astrolift_deployments(info, app_slug=slug)


def _deployment(info, guid):
    return LifecycleQuery().astrolift_deployment(info, id=str(guid))


def _environments(info, slug):
    return LifecycleQuery().astrolift_environments(info, app_slug=slug)


# ---------------------------------------------------------------------
# keyed by app slug
# ---------------------------------------------------------------------


def test_app_scoped_binding_lists_its_own_deployments(world, reba, info):
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="lc-app-reader-1731",
    )
    with as_tenant(world, reba):
        assert _deployments(info, world.medops_app.slug) is not None


def test_app_scoped_binding_cannot_list_a_siblings_deployments(world, reba, info):
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="lc-app-sib-1731",
    )
    with as_tenant(world, reba), pytest.raises(PermissionDenied):
        _deployments(info, world.platform_app.slug)


def test_team_scoped_binding_reaches_that_teams_app(world, reba, info):
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="TEAM",
        scope_id=world.medops.id,
        slug="lc-team-1731",
    )
    with as_tenant(world, reba):
        assert _environments(info, world.medops_app.slug) is not None


# ---------------------------------------------------------------------
# keyed by a row that belongs to the app
# ---------------------------------------------------------------------


def test_a_deployment_guid_resolves_to_its_own_app(world, reba, info):
    """The walk that makes approve / rollback / abort reachable."""
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="lc-dep-1731",
    )
    with as_tenant(world, reba):
        assert _deployment(info, world.medops_deployment.guid) is not None


def test_a_deployment_guid_from_a_sibling_app_is_denied(world, reba, info):
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="lc-dep-sib-1731",
    )
    with as_tenant(world, reba), pytest.raises(PermissionDenied):
        _deployment(info, world.platform_deployment.guid)


def test_a_deployment_guid_from_another_org_is_denied(world, reba, info):
    """Confinement is in the scope lookup, not only in the resolver's
    own join -- the check runs before the resolver body."""
    elsewhere = ScopeWorld("lc1731b")
    env = AppEnvironment.objects.create(
        registered_app=elsewhere.medops_app,
        tenant_cluster=make_cluster(elsewhere, "lc1731b"),
        name="prod",
    )
    foreign = Deployment.objects.create(
        registered_app=elsewhere.medops_app, app_environment=env, image_tag="v1"
    )
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=elsewhere.medops_app.id,
        slug="lc-foreign-1731",
    )
    with as_tenant(world, reba), pytest.raises(PermissionDenied):
        _deployment(info, foreign.guid)


def test_an_unknown_guid_falls_back_to_the_stricter_org_check(world, reba, info):
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=world.medops_app.id,
        slug="lc-unknown-1731",
    )
    with as_tenant(world, reba), pytest.raises(PermissionDenied):
        _deployment(info, "01920000-0000-7000-8000-000000000000")


def test_org_scoped_binding_still_reaches_everything(world, reba, info):
    bind_role(
        reba,
        permissions=[Permission.APP_READ],
        kind="ORG",
        scope_id=world.org.id,
        slug="lc-org-1731",
    )
    with as_tenant(world, reba):
        assert _deployment(info, world.medops_deployment.guid) is not None
        assert _deployment(info, world.platform_deployment.guid) is not None
        assert _deployments(info, world.platform_app.slug) is not None


def test_no_binding_anywhere_is_still_denied(world, reba, info):
    with as_tenant(world, reba), pytest.raises(PermissionDenied):
        _deployments(info, world.medops_app.slug)
