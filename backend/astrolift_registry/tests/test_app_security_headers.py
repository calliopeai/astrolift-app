"""The per-app security headers switch: model default, type field, mutation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_registry.tests.test_edge_public_2504 import (  # noqa: F401
    _no_edge_workflow,
    _no_opensearch_profile_index,
    _world,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reapplied(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "astrolift_registry.schema.app_security_headers._reapply_app",
        lambda app, info: calls.append(app.slug),
    )
    return calls


def _info():
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None), user=None))


def _set(world, enabled, *, org=None, slug=None):
    from astrolift_registry.schema.app_security_headers import (
        AppSecurityHeadersMutation,
        SetAppSecurityHeadersInput,
    )

    with tenant_context(TenantContext(organization_id=(org or world.org).pk)):
        return AppSecurityHeadersMutation().set_app_security_headers(
            _info(), SetAppSecurityHeadersInput(app_slug=slug or world.app.slug, enabled=enabled)
        )


def test_the_default_is_off_with_a_database_default():
    from astrolift_registry.models import RegisteredApp

    field = RegisteredApp._meta.get_field("security_headers")
    assert field.default is False and field.db_default is False
    assert _world().app.security_headers is False


def test_the_switch_sets_clears_and_reapplies(permission_resolver, _reapplied):
    world = _world()
    permission_resolver.grant(Permission.APP_ACCESS)
    result = _set(world, True)
    assert result.ok is True, result.errors
    assert result.data.security_headers is True
    world.app.refresh_from_db()
    assert world.app.security_headers is True
    assert _reapplied == [world.app.slug]
    assert _set(world, False).data.security_headers is False
    world.app.refresh_from_db()
    assert world.app.security_headers is False
    assert _reapplied == [world.app.slug, world.app.slug]


def test_the_switch_is_idempotent(permission_resolver, _reapplied):
    world = _world()
    permission_resolver.grant(Permission.APP_ACCESS)
    assert _set(world, False).ok is True
    assert _reapplied == []
    _set(world, True)
    version = type(world.app).objects.get(pk=world.app.pk).version
    assert _set(world, True).data.security_headers is True
    assert type(world.app).objects.get(pk=world.app.pk).version == version
    assert _reapplied == [world.app.slug]


def test_an_unknown_app_is_not_found(permission_resolver):
    world = _world()
    permission_resolver.grant(Permission.APP_ACCESS)
    result = _set(world, True, slug="nope")
    assert result.ok is False and result.errors[0].code == "NOT_FOUND"


def test_another_orgs_app_is_not_found(permission_resolver):
    world, other = _world(slug="one"), _world(slug="two")
    permission_resolver.grant(Permission.APP_ACCESS)
    result = _set(world, True, org=other.org)
    assert result.ok is False and result.errors[0].code == "NOT_FOUND"
    world.app.refresh_from_db()
    assert world.app.security_headers is False


def test_the_switch_needs_app_access(permission_resolver):
    world = _world()
    permission_resolver.grant(Permission.APP_UPDATE)
    result = _set(world, True)
    assert result.ok is False and result.errors[0].code == "PERMISSION_DENIED"
    world.app.refresh_from_db()
    assert world.app.security_headers is False


def test_each_state_is_audited_under_its_own_action(permission_resolver, monkeypatch):
    from core import mutations

    seen = []
    monkeypatch.setattr(mutations, "emit_audit", lambda entry: seen.append(entry.action))
    world = _world()
    permission_resolver.grant(Permission.APP_ACCESS)
    _set(world, True)
    _set(world, False)
    assert seen == ["app.security_headers.enable", "app.security_headers.disable"]


def test_the_access_query_reads_the_switch(permission_resolver):
    from astrolift_registry.schema.app_access import AppAccessQuery

    world = _world()
    permission_resolver.grant(Permission.APP_READ)
    with tenant_context(TenantContext(organization_id=world.org.pk)):
        assert (
            AppAccessQuery().astrolift_app_access(_info(), app_slug=world.app.slug).security_headers is False
        )
        world.app.security_headers = True
        world.app.save()
        assert (
            AppAccessQuery().astrolift_app_access(_info(), app_slug=world.app.slug).security_headers is True
        )
