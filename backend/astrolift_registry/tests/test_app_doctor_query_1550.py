"""`astroliftAppDoctor` exposes the doctor that nothing could reach (#1550).

`services/app_doctor.py` implements seven checks and has had passing tests
since it was written. Every caller was a test:

    $ grep -rn "run_app_doctor" --include=*.py --include=*.tsx .
    backend/astrolift_lifecycle/tests/test_app_doctor_1550.py   (7 hits)
    backend/astrolift_registry/services/app_doctor.py:14        (its docstring)

So the checks in #1550's list were written and green, and an operator asking
"is this app fully wired?" still had no way to run them. These tests pin the
exposure: the query returns the service's verdict, and it is org-scoped
before any probe runs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _app(org, *, slug="hello-app"):
    team = Team.objects.create(organization=org, name=f"Eng {slug}", slug=f"eng-{slug}")
    project = Project.objects.create(organization=org, team=team, name=f"Demo {slug}", slug=f"demo-{slug}")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug=slug,
        source_kind="github",
        source_repo="acme/api",
        default_branch="main",
        deploy_branch="main",
        manifest_path="astrolift.toml",
        k8s_namespace=f"acme-{slug}",
        subdomain=slug,
        registry_repo_uri="123456.dkr.ecr.us-east-1.amazonaws.com/hello-app",
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-doctor")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Rival", slug="rival-doctor")


def test_the_query_returns_the_services_checks(org, permission_resolver):
    """The regression: this had no resolver at all."""
    app = _app(org)
    permission_resolver.grant(Permission.APP_READ)

    with tenant_context(TenantContext(organization_id=org.id)):
        report = RegistryQuery().astrolift_app_doctor(_info(), app_slug=app.slug)

    assert report.checks, "the doctor reported nothing at all"

    # The keys are the service's own contract, so pin them by name rather
    # than trusting a count.
    keys = {c.key for c in report.checks}
    for expected in ("manifest", "autowire", "registry_repo", "push_role", "dns", "deployments"):
        assert expected in keys, f"missing the {expected} check: {sorted(keys)}"


def test_every_check_carries_a_usable_status(org, permission_resolver):
    app = _app(org)
    permission_resolver.grant(Permission.APP_READ)

    with tenant_context(TenantContext(organization_id=org.id)):
        report = RegistryQuery().astrolift_app_doctor(_info(), app_slug=app.slug)

    allowed = {"pass", "fail", "warn", "skip", "unknown"}
    for check in report.checks:
        assert check.status in allowed, f"{check.key} -> {check.status}"
        assert check.detail, f"{check.key} has no detail for an operator to read"


def test_healthy_is_the_services_rollup_not_a_client_guess(org, permission_resolver):
    """`healthy` must agree with the statuses in the same payload, so the UI
    never has to recompute it -- and `unknown` must not read as healthy."""
    app = _app(org)
    permission_resolver.grant(Permission.APP_READ)

    with tenant_context(TenantContext(organization_id=org.id)):
        report = RegistryQuery().astrolift_app_doctor(_info(), app_slug=app.slug)

    expected = all(c.status in ("pass", "skip") for c in report.checks)
    assert report.healthy is expected


def test_another_tenants_app_is_not_reachable(org, other_org, permission_resolver):
    """The checks read a push-role trust policy and resolve hostnames, so
    aiming them at a foreign app would be a cross-tenant probe."""
    foreign = _app(other_org, slug="rival-app")
    permission_resolver.grant(Permission.APP_READ)

    with tenant_context(TenantContext(organization_id=org.id)):
        report = RegistryQuery().astrolift_app_doctor(_info(), app_slug=foreign.slug)

    assert report.checks == []
    assert report.healthy is False


def test_an_unknown_slug_reports_nothing_rather_than_raising(org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)

    with tenant_context(TenantContext(organization_id=org.id)):
        report = RegistryQuery().astrolift_app_doctor(_info(), app_slug="no-such-app")

    assert report.checks == []
    assert report.healthy is False


def test_it_requires_app_read(org):
    app = _app(org)

    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(Exception) as exc:
            RegistryQuery().astrolift_app_doctor(_info(), app_slug=app.slug)

    # Queries raise rather than returning an envelope; what matters is that
    # the gate fires before any probe runs, and that it names the permission
    # it wanted (the message is "no actor: app.read" with no viewer).
    assert "app.read" in str(exc.value)
