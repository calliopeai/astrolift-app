"""Tests for ``retryAstroliftAutowire`` (#1108).

The detail-page "Retry autowire" affordance: re-runs the same ``run_autowire``
chain registration fires, so an app that onboarded half-wired can be completed
with one click. The wire-level services are mocked (each has its own coverage);
the permission gate, envelope mapping, and payload shape run for real.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    RetryAstroliftAutowireInput,
)
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.secrets import PushSecretsResult, ValidateCiSecretsResult
from astrolift_scm.services.webhooks import InstallSourceWebhookResult
from astrolift_scm.services.workflow_sync import WorkflowSyncResult
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(user=None):
    request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


@pytest.fixture
def app_with_repo(app):
    app.source_kind = "github"
    app.source_repo = "acme/api"
    app.save(update_fields=["source_kind", "source_repo", "updated_at", "version"])
    return app


@pytest.fixture
def app_conn(org):
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


def test_retry_completes_autowire(monkeypatch, permission_resolver, org, app_with_repo, app_conn):
    permission_resolver.grant(Permission.APP_UPDATE)
    _patch_wire_ok(monkeypatch)

    with _ctx(org):
        result = LifecycleMutation().retry_astrolift_autowire(
            _info(),
            input=RetryAstroliftAutowireInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.connected is True
    assert result.data.all_ok is True
    assert result.data.ci_workflow == "ok"
    assert result.data.webhook == "ok"
    assert result.data.secrets == "ok"
    assert result.data.detail == ""


def test_retry_unwired_is_success_with_connected_false(monkeypatch, permission_resolver, org, app_with_repo):
    """No org connection → still a successful mutation, connected=false."""
    permission_resolver.grant(Permission.APP_UPDATE)

    def boom(*a, **k):
        raise AssertionError("no connection → no wiring service should run")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo", boom)

    with _ctx(org):
        result = LifecycleMutation().retry_astrolift_autowire(
            _info(),
            input=RetryAstroliftAutowireInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.connected is False
    assert result.data.all_ok is False


def test_retry_permission_denied(monkeypatch, permission_resolver, org, app_with_repo):
    def boom(*a, **k):
        raise AssertionError("must not run autowire without permission")

    monkeypatch.setattr("astrolift_scm.services.autowire.run_autowire", boom)

    with _ctx(org):
        result = LifecycleMutation().retry_astrolift_autowire(
            _info(),
            input=RetryAstroliftAutowireInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_retry_app_not_found(permission_resolver, org):
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = LifecycleMutation().retry_astrolift_autowire(
            _info(),
            input=RetryAstroliftAutowireInput(app_slug="no-such-app"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"
