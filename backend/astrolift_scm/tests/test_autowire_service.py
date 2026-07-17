"""Tests for the autowire orchestration service (#1108).

``run_autowire`` chains CI-workflow → webhook → secrets after registration.
These tests pin the ORCHESTRATION contract — call fan-out, per-step outcome
recording, resilience (one step failing doesn't abort the others), the
"connect for auto-deploy" gate, and idempotent re-runs — with the three
wire-level services mocked (each has its own end-to-end tests). Everything
above the mocks (connection resolution, ``autowire_state`` persistence) runs
against real Postgres rows.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.autowire import run_autowire
from astrolift_scm.services.secrets import PushSecretsResult, ValidateCiSecretsResult
from astrolift_scm.services.webhooks import InstallSourceWebhookResult
from astrolift_scm.services.workflow_sync import WorkflowSyncResult
from core.secrets import encrypt_at_rest

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
def org():
    return Organization.objects.create(name="Acme", slug="acme-aw")


def _make_app(org, *, repo="acme/api"):
    team = Team.objects.create(organization=org, name="Eng", slug="eng-aw")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p-aw")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug="app-aw",
        source_kind="github",
        source_repo=repo,
    )


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


def _oauth_conn(org):
    encrypted = encrypt_at_rest(b"gho_token")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name="GitHub: alice",
        account_login="alice",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _patch_all_ok(monkeypatch, calls):
    """Patch every wire-level service to a recorded success."""
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo",
        lambda app, viewer_user=None: calls.append("workflow") or WorkflowSyncResult(status="created"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.webhooks.reconcile_source_webhook_state",
        lambda app: calls.append("reconcile") or "app_delivers",
    )
    monkeypatch.setattr(
        "astrolift_scm.services.webhooks.install_astrolift_source_webhook",
        lambda app: calls.append("webhook") or InstallSourceWebhookResult(status="created", hook_id="42"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.secrets.push_astrolift_ci_secrets",
        lambda app, viewer_user=None, platform_api_url="": calls.append("secrets")
        or PushSecretsResult(ok=True, secret_names=("ASTROLIFT_DEPLOY_TOKEN",), new_token_last_4="9999"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.secrets.validate_astrolift_ci_secrets",
        lambda app, viewer_user=None: calls.append("validate")
        or ValidateCiSecretsResult(ok=True, results=()),
    )


def test_chains_all_three_when_app_connection_present(monkeypatch, org):
    app = _make_app(org)
    _app_conn(org)
    calls: list[str] = []
    _patch_all_ok(monkeypatch, calls)

    outcome = run_autowire(app)

    assert outcome.connected is True
    assert outcome.all_ok is True
    assert outcome.steps == {"ci_workflow": "ok", "webhook": "ok", "secrets": "ok"}
    # All three top-level steps ran, workflow before webhook before secrets.
    assert calls.index("workflow") < calls.index("webhook") < calls.index("secrets")
    # The phantom reconcile runs inside the webhook step, before install.
    assert calls.index("reconcile") < calls.index("webhook")

    # Persisted snapshot the detail page reads back.
    app.refresh_from_db()
    assert app.autowire_state["connected"] is True
    assert app.autowire_state["ci_workflow"] == "ok"
    assert app.autowire_state["webhook"] == "ok"
    assert app.autowire_state["secrets"] == "ok"
    assert "checked_at" in app.autowire_state
    assert "errors" not in app.autowire_state


def test_no_connection_lands_unwired(monkeypatch, org):
    app = _make_app(org)  # no SourceConnection created

    def boom(*a, **k):
        raise AssertionError("no org connection → no wiring service should run")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo", boom)
    monkeypatch.setattr("astrolift_scm.services.webhooks.install_astrolift_source_webhook", boom)
    monkeypatch.setattr("astrolift_scm.services.secrets.push_astrolift_ci_secrets", boom)

    outcome = run_autowire(app)

    assert outcome.connected is False
    assert outcome.steps == {}
    assert outcome.all_ok is False
    app.refresh_from_db()
    assert app.autowire_state["connected"] is False


def test_no_source_repo_is_unwired(monkeypatch, org):
    app = _make_app(org, repo="")
    _app_conn(org)

    def boom(*a, **k):
        raise AssertionError("no source repo → nothing to wire")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo", boom)

    outcome = run_autowire(app)

    assert outcome.connected is False
    assert outcome.steps == {}


def test_step_failure_is_isolated(monkeypatch, org):
    """A webhook failure must not abort the workflow or secrets steps."""
    app = _make_app(org)
    _app_conn(org)
    calls: list[str] = []
    _patch_all_ok(monkeypatch, calls)

    def blow_up(app):
        calls.append("webhook")
        raise RuntimeError("github 500")

    monkeypatch.setattr("astrolift_scm.services.webhooks.install_astrolift_source_webhook", blow_up)

    outcome = run_autowire(app)

    assert outcome.connected is True
    assert outcome.all_ok is False
    assert outcome.steps["ci_workflow"] == "ok"
    assert outcome.steps["webhook"] == "error"
    assert outcome.steps["secrets"] == "ok"  # ran despite the webhook failure
    assert "github 500" in outcome.errors["webhook"]
    # Workflow ran before the failing webhook; secrets ran after it.
    assert "workflow" in calls and "secrets" in calls

    app.refresh_from_db()
    assert app.autowire_state["webhook"] == "error"
    assert app.autowire_state["errors"]["webhook"]


def test_secrets_requires_the_org_app(monkeypatch, org):
    """An org with only an OAuth connection can wire webhook+workflow but not
    secrets (secrets need the App). That surfaces as a clean secrets error,
    and the push service is never even reached."""
    app = _make_app(org)
    _oauth_conn(org)  # ORG_REPO_WRITE resolvable, but no App → no PLATFORM_REPO_WRITE
    calls: list[str] = []
    _patch_all_ok(monkeypatch, calls)

    outcome = run_autowire(app)

    assert outcome.connected is True
    assert outcome.steps["ci_workflow"] == "ok"
    assert outcome.steps["webhook"] == "ok"
    assert outcome.steps["secrets"] == "error"
    assert "GitHub App" in outcome.errors["secrets"]
    # The push service was short-circuited — never called.
    assert "secrets" not in calls


def test_idempotent_rerun_keeps_wired_state(monkeypatch, org):
    """Re-running against an already-wired app stays green and doesn't churn
    the recorded hook id — the underlying services are idempotent (in_sync /
    refreshed / live) and the orchestrator just re-drives them."""
    app = _make_app(org)
    app.source_webhook_id = "42"
    app.save(update_fields=["source_webhook_id", "updated_at", "version"])
    _app_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo",
        lambda app, viewer_user=None: WorkflowSyncResult(status="in_sync"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.webhooks.reconcile_source_webhook_state",
        lambda app: "live",
    )
    monkeypatch.setattr(
        "astrolift_scm.services.webhooks.install_astrolift_source_webhook",
        lambda app: InstallSourceWebhookResult(status="refreshed", hook_id="42"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.secrets.push_astrolift_ci_secrets",
        lambda app, viewer_user=None, platform_api_url="": PushSecretsResult(ok=True, new_token_last_4="1"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.secrets.validate_astrolift_ci_secrets",
        lambda app, viewer_user=None: ValidateCiSecretsResult(ok=True, results=()),
    )

    first = run_autowire(app)
    second = run_autowire(app)

    assert first.all_ok is True
    assert second.all_ok is True
    app.refresh_from_db()
    assert app.source_webhook_id == "42"  # no churn
    assert app.autowire_state["webhook"] == "ok"


def test_validate_downgrade_only_on_definitive_empty(monkeypatch, org):
    """A successful push stays ``ok`` even if validate errors, but flips to
    ``error`` on the unambiguous "pushed but the repo reports none set" signal."""
    app = _make_app(org)
    _app_conn(org)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo",
        lambda app, viewer_user=None: WorkflowSyncResult(status="created"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.webhooks.reconcile_source_webhook_state", lambda app: "app_delivers"
    )
    monkeypatch.setattr(
        "astrolift_scm.services.webhooks.install_astrolift_source_webhook",
        lambda app: InstallSourceWebhookResult(status="app_delivers"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.secrets.push_astrolift_ci_secrets",
        lambda app, viewer_user=None, platform_api_url="": PushSecretsResult(ok=True, new_token_last_4="1"),
    )

    # validate reports every canonical secret as NOT set → definitive failure.
    from astrolift_scm.services.secrets import CiSecretValidation

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.validate_astrolift_ci_secrets",
        lambda app, viewer_user=None: ValidateCiSecretsResult(
            ok=True,
            results=(CiSecretValidation(secret_name="ASTROLIFT_DEPLOY_TOKEN", is_set=False),),
        ),
    )

    outcome = run_autowire(app)

    assert outcome.steps["secrets"] == "error"
    assert "none are set" in outcome.errors["secrets"]
