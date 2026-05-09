"""Tests for setAppSubdomain (#52)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import (
    RegistryMutation,
    SetAppSubdomainInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug="demo"
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
        subdomain="hello",
    )
    return org, team, project, app


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_set_subdomain_happy_path(permission_resolver):
    org, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().set_app_subdomain(
            _info(),
            input=SetAppSubdomainInput(id=str(app.guid), subdomain="hello-v2"),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.subdomain == "hello-v2"


def test_set_subdomain_normalizes_case(permission_resolver):
    """Mixed-case + whitespace input lowercases through the validator."""
    org, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().set_app_subdomain(
            _info(),
            input=SetAppSubdomainInput(id=str(app.guid), subdomain="  HELLO-NEW  "),
        )
    assert result.ok
    app.refresh_from_db()
    assert app.subdomain == "hello-new"


def test_set_subdomain_rejects_invalid_label(permission_resolver):
    org, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().set_app_subdomain(
            _info(),
            input=SetAppSubdomainInput(id=str(app.guid), subdomain="-bad-"),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "subdomain"
    assert "DNS label" in result.errors[0].message


@pytest.mark.parametrize(
    "reserved",
    ["api", "admin", "auth", "dashboard", "metrics", "www", "WWW"],
)
def test_set_subdomain_rejects_reserved(permission_resolver, reserved):
    org, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().set_app_subdomain(
            _info(),
            input=SetAppSubdomainInput(id=str(app.guid), subdomain=reserved),
        )
    assert not result.ok
    assert "reserved" in result.errors[0].message.lower()


def test_set_subdomain_rejects_within_org_collision(permission_resolver):
    """Two apps in the same org may not share a subdomain — DNS would
    race for the same label."""
    org, team, project, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    other = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Other",
        slug="other-app",
        provisioning_status="ready",
        subdomain="taken",
    )

    with _ctx(org):
        result = RegistryMutation().set_app_subdomain(
            _info(),
            input=SetAppSubdomainInput(id=str(app.guid), subdomain="taken"),
        )
    assert not result.ok
    assert result.errors[0].code == "CONFLICT"
    assert other.slug == "other-app"  # silence unused


def test_set_subdomain_is_idempotent(permission_resolver):
    """Setting the same value twice should not bump version a second
    time — saves are gated on a value change."""
    org, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        RegistryMutation().set_app_subdomain(
            _info(),
            input=SetAppSubdomainInput(id=str(app.guid), subdomain="hello"),
        )
    app.refresh_from_db()
    version_after = app.version

    with _ctx(org):
        RegistryMutation().set_app_subdomain(
            _info(),
            input=SetAppSubdomainInput(id=str(app.guid), subdomain="hello"),
        )
    app.refresh_from_db()
    assert app.version == version_after


def test_set_subdomain_requires_permission():
    org, _, _, app = _scaffold()
    with _ctx(org):
        result = RegistryMutation().set_app_subdomain(
            _info(),
            input=SetAppSubdomainInput(id=str(app.guid), subdomain="ok"),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
