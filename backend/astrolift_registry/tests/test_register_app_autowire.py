"""Register-app autowire integration (#1108).

Proves ``register_app`` now completes the autowire (CI workflow → webhook →
secrets) straight from registration instead of leaving apps half-wired, and
that it does so resiliently: a wiring failure — or no org connection at all —
never fails the registration. The wire-level services are mocked (each has its
own end-to-end coverage); everything above them runs against real Postgres.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.secrets import PushSecretsResult, ValidateCiSecretsResult
from astrolift_scm.services.webhooks import InstallSourceWebhookResult
from astrolift_scm.services.workflow_sync import WorkflowSyncResult
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _scaffold_org(slug="autowire"):
    org = Organization.objects.create(name="Acme", slug=f"acme-{slug}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{slug}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{slug}")
    return org, project


def _app_conn(org):
    encrypted = encrypt_at_rest(b"fake-pem")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name="GitHub App: acme",
        account_login="acme",
        installation_id="987",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _patch_wire_ok(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo",
        lambda app, viewer_user=None: WorkflowSyncResult(status="created"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.webhooks.reconcile_source_webhook_state",
        lambda app: "app_delivers",
    )
    monkeypatch.setattr(
        "astrolift_scm.services.webhooks.install_astrolift_source_webhook",
        lambda app: InstallSourceWebhookResult(status="created", hook_id="42"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.secrets.push_astrolift_ci_secrets",
        lambda app, viewer_user=None, platform_api_url="": PushSecretsResult(ok=True, new_token_last_4="1"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.secrets.validate_astrolift_ci_secrets",
        lambda app, viewer_user=None: ValidateCiSecretsResult(ok=True, results=()),
    )


def test_register_with_connection_completes_autowire(monkeypatch, permission_resolver, seed_cluster):
    org, project = _scaffold_org("with-conn")
    seed_cluster(org, slug="aw-with")
    _app_conn(org)
    _patch_wire_ok(monkeypatch)
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="App",
                slug="wired-app",
                source_repo="acme/wired-app",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="wired-app")
    # Register chained the full autowire and persisted the green snapshot.
    assert app.autowire_state["connected"] is True
    assert app.autowire_state["ci_workflow"] == "ok"
    assert app.autowire_state["webhook"] == "ok"
    assert app.autowire_state["secrets"] == "ok"
    assert "errors" not in app.autowire_state


def test_register_without_connection_lands_unwired(monkeypatch, permission_resolver, seed_cluster):
    org, project = _scaffold_org("no-conn")
    seed_cluster(org, slug="aw-none")
    # No SourceConnection → the "connect for auto-deploy" state.
    permission_resolver.grant(Permission.APP_CREATE)

    def boom(*a, **k):
        raise AssertionError("no connection → no wiring service should run")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo", boom)
    monkeypatch.setattr("astrolift_scm.services.webhooks.install_astrolift_source_webhook", boom)
    monkeypatch.setattr("astrolift_scm.services.secrets.push_astrolift_ci_secrets", boom)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="App",
                slug="unwired-app",
                source_repo="acme/unwired-app",
            ),
        )

    # Registration still succeeds — the app is deployable by hand.
    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="unwired-app")
    assert app.autowire_state["connected"] is False
    assert "ci_workflow" not in app.autowire_state


def test_register_autowire_failure_does_not_fail_registration(monkeypatch, permission_resolver, seed_cluster):
    org, project = _scaffold_org("fail")
    seed_cluster(org, slug="aw-fail")
    _app_conn(org)
    _patch_wire_ok(monkeypatch)
    # The CI-workflow step blows up mid-chain.
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo",
        lambda app, viewer_user=None: (_ for _ in ()).throw(RuntimeError("github down")),
    )
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="App",
                slug="resilient-app",
                source_repo="acme/resilient-app",
            ),
        )

    # The registration must NOT roll back on a wiring failure.
    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="resilient-app")
    assert app.autowire_state["ci_workflow"] == "error"
    assert app.autowire_state["errors"]["ci_workflow"]
    # The later steps still ran.
    assert app.autowire_state["webhook"] == "ok"
    assert app.autowire_state["secrets"] == "ok"
