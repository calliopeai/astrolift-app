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


@pytest.fixture(autouse=True)
def _temporal_disabled(settings):
    """The mutation enqueues SyncAppDomainWorkflow now (#143). There is no
    Temporal server in the unit-test env, so run the client facade in its
    no-op mode; the two tests that assert on the enqueue replace
    ``start_workflow`` outright."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
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


def _record_starts(monkeypatch):
    """Capture what the mutation enqueues on the Temporal client facade.

    The resolver imports ``start_workflow`` from the client module inside
    the function body, so patching the module attribute is enough.
    """
    import astrolift_workflows.client as wf_client

    starts: list[tuple[str, list, str]] = []

    def _start(name, args, *, workflow_id, task_queue=None):
        starts.append((name, list(args), workflow_id))
        return wf_client.WorkflowHandle(
            workflow_id=workflow_id,
            run_id="run-1",
            enqueued=True,
        )

    monkeypatch.setattr(wf_client, "start_workflow", _start)
    return starts


def test_set_subdomain_starts_the_domain_sync_workflow(permission_resolver, monkeypatch):
    """#143: the row write alone left DNS + the Ingress host rule on the
    old hostname. The rename must enqueue SyncAppDomainWorkflow, carrying
    the pre-write subdomain so the workflow can diff and roll back."""
    org, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    starts = _record_starts(monkeypatch)

    with _ctx(org):
        result = RegistryMutation().set_app_subdomain(
            _info(),
            input=SetAppSubdomainInput(id=str(app.guid), subdomain="hello-v2"),
        )

    assert result.ok, result.errors
    assert len(starts) == 1, "the rename must fire the sync, not just write the row"
    name, args, workflow_id = starts[0]
    assert name == "SyncAppDomainWorkflow"
    assert workflow_id == f"SyncAppDomainWorkflow-{app.guid}"
    assert args[0].registered_app_id == app.pk
    assert args[0].previous_subdomain == "hello"


def test_unchanged_subdomain_starts_nothing(permission_resolver, monkeypatch):
    """No hostname moved, so there is nothing to reconcile - firing here
    would re-apply an app's manifests on every no-op save."""
    org, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    starts = _record_starts(monkeypatch)

    with _ctx(org):
        result = RegistryMutation().set_app_subdomain(
            _info(),
            input=SetAppSubdomainInput(id=str(app.guid), subdomain="hello"),
        )

    assert result.ok
    assert starts == []
