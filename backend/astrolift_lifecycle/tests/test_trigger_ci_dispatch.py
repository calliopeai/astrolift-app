"""Tests for ``triggerAstroliftDeployWorkflow`` (#387).

Covers the mutation surface end-to-end: the GitHub workflow-dispatch
HTTP call is stubbed at ``urllib.request.urlopen``; everything above
the wire (connection picker, approval gate, permission gate, error
mapping) runs against real Postgres rows.
"""

from __future__ import annotations

import io
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    TriggerDeployWorkflowInput,
)
from astrolift_scm.models import SourceConnection
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Local fixtures + helpers
# ---------------------------------------------------------------------------


def _info(user=None):
    """Minimal `info`-shaped object accepted by every mutation resolver.

    The trigger mutation reads ``info.context.request.user`` to record
    the actor in the audit row; a ``None`` user is fine for the
    permission-gated tests because the resolver's permission decorator
    runs before audit recording."""
    request = SimpleNamespace(user=user)
    context = SimpleNamespace(request=request, user=user)
    return SimpleNamespace(context=context)


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


@pytest.fixture
def app_with_repo(app):
    """The base ``app`` fixture has no source repo configured — the
    dispatch path requires one. Tweak it in place so we share the
    surrounding org / team / project scaffolding."""
    app.source_kind = "github"
    app.source_repo = "acme/api"
    app.default_branch = "main"
    app.deploy_branch = "main"
    app.save(
        update_fields=[
            "source_kind",
            "source_repo",
            "default_branch",
            "deploy_branch",
            "updated_at",
            "version",
        ]
    )
    return app


@pytest.fixture
def github_connection(org):
    encrypted = encrypt_at_rest(b"gho_test_token_never_hits_the_network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name="GitHub: alice",
        account_login="alice",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _http_error(code: int, body: bytes = b"", url: str = "https://example") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))


class _Response204:
    """Context-manager that mimics urlopen's success shape for the
    workflow-dispatch endpoint, which returns 204 No Content."""

    def __enter__(self):
        return SimpleNamespace(read=lambda: b"", status=204)

    def __exit__(self, *_):
        return False


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_trigger_dispatches_workflow_and_returns_run_url(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """Permission granted + connection present + workflow exists →
    the mutation POSTs to the right URL with ``ref=<deploy_branch>``
    and returns the synthesized runs-page URL."""
    permission_resolver.grant(Permission.APP_DEPLOY)

    captured: dict = {}

    def fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["auth"] = req.get_header("Authorization") or ""
        captured["body"] = req.data
        return _Response204()

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        fake_urlopen,
    )

    with _ctx(org):
        result = LifecycleMutation().trigger_astrolift_deploy_workflow(
            _info(),
            input=TriggerDeployWorkflowInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.dispatched_branch == "main"
    assert result.data.run_url == ("https://github.com/acme/api/actions/workflows/astrolift-ci.yml")
    # Right endpoint shape: /repos/{owner}/{repo}/actions/workflows/{file}/dispatches
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/repos/acme/api/actions/workflows/astrolift-ci.yml/dispatches")
    assert captured["auth"].startswith("token ")
    # Body is JSON-encoded; just check the branch slot.
    assert b'"ref": "main"' in captured["body"]


def test_trigger_uses_explicit_branch_when_passed(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """An explicit ``branch`` overrides the app's deploy_branch."""
    permission_resolver.grant(Permission.APP_DEPLOY)

    captured: dict = {}

    def fake_urlopen(req, timeout=15):
        captured["body"] = req.data
        return _Response204()

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        fake_urlopen,
    )

    with _ctx(org):
        result = LifecycleMutation().trigger_astrolift_deploy_workflow(
            _info(),
            input=TriggerDeployWorkflowInput(
                app_slug=app_with_repo.slug,
                branch="release/2026.05",
            ),
        )

    assert result.ok, result.errors
    assert result.data.dispatched_branch == "release/2026.05"
    assert b'"ref": "release/2026.05"' in captured["body"]


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_trigger_workflow_file_missing_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """GitHub 404 with ``workflow`` in the body → WORKFLOW_FILE_MISSING
    → PRECONDITION envelope. The FE pivots to the 'sync CI workflow'
    affordance from this."""
    permission_resolver.grant(Permission.APP_DEPLOY)

    def fake_urlopen(req, timeout=15):
        raise _http_error(404, b'{"message":"Workflow does not exist"}', req.full_url)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        fake_urlopen,
    )

    with _ctx(org):
        result = LifecycleMutation().trigger_astrolift_deploy_workflow(
            _info(),
            input=TriggerDeployWorkflowInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "workflow" in result.errors[0].message.lower()
    assert "sync" in result.errors[0].message.lower()


def test_trigger_no_source_connection_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
):
    """No SourceConnection rows in the app's org → PRECONDITION,
    with a guidance message about connecting a GitHub identity."""
    permission_resolver.grant(Permission.APP_DEPLOY)

    # Guard: if the resolver somehow reaches the network, fail loudly.
    def boom(*a, **kw):
        raise AssertionError("should not call network when no connection exists")

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        boom,
    )

    with _ctx(org):
        result = LifecycleMutation().trigger_astrolift_deploy_workflow(
            _info(),
            input=TriggerDeployWorkflowInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "connection" in result.errors[0].message.lower()


def test_trigger_approval_required_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
    env_requires_approval,  # creates an env with required_approvals=1
):
    """The env-level approval gate trips even when the operator has
    APP_DEPLOY — the mutation must not bypass the approver flow."""
    permission_resolver.grant(Permission.APP_DEPLOY)

    def boom(*a, **kw):
        raise AssertionError("must not dispatch when approvals required")

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        boom,
    )

    with _ctx(org):
        result = LifecycleMutation().trigger_astrolift_deploy_workflow(
            _info(),
            input=TriggerDeployWorkflowInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "approval" in result.errors[0].message.lower()
    assert "startdeployment" in result.errors[0].message.lower()


def test_trigger_app_level_approval_required_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """``app.requires_approval=True`` should also block, even with no
    env-level approvals configured."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    app_with_repo.requires_approval = True
    app_with_repo.save(update_fields=["requires_approval", "updated_at", "version"])

    def boom(*a, **kw):
        raise AssertionError("must not dispatch when app gates on approval")

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        boom,
    )

    with _ctx(org):
        result = LifecycleMutation().trigger_astrolift_deploy_workflow(
            _info(),
            input=TriggerDeployWorkflowInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "approval" in result.errors[0].message.lower()


def test_trigger_permission_denied_without_app_deploy(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """No APP_DEPLOY grant → PERMISSION_DENIED, never touches the host."""

    def boom(*a, **kw):
        raise AssertionError("should not reach network without permission")

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        boom,
    )

    with _ctx(org):
        result = LifecycleMutation().trigger_astrolift_deploy_workflow(
            _info(),
            input=TriggerDeployWorkflowInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_trigger_app_not_found(monkeypatch, permission_resolver, org):
    permission_resolver.grant(Permission.APP_DEPLOY)

    with _ctx(org):
        result = LifecycleMutation().trigger_astrolift_deploy_workflow(
            _info(),
            input=TriggerDeployWorkflowInput(app_slug="no-such-app"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"


def test_trigger_auth_failure_maps_to_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """A 401 from the host → AUTH_FAILED → surfaces as PRECONDITION
    with an actionable message."""
    permission_resolver.grant(Permission.APP_DEPLOY)

    def fake_urlopen(req, timeout=15):
        raise _http_error(401, b'{"message":"bad creds"}', req.full_url)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        fake_urlopen,
    )

    with _ctx(org):
        result = LifecycleMutation().trigger_astrolift_deploy_workflow(
            _info(),
            input=TriggerDeployWorkflowInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "rejected" in result.errors[0].message.lower()


def test_trigger_gitlab_raises_not_implemented_clean(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """A GitLab-sourced app surfaces a clean PRECONDITION envelope
    (not a 500) explaining the GitLab gap."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    app_with_repo.source_kind = "gitlab"
    app_with_repo.save(update_fields=["source_kind", "updated_at", "version"])

    with _ctx(org):
        result = LifecycleMutation().trigger_astrolift_deploy_workflow(
            _info(),
            input=TriggerDeployWorkflowInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "gitlab" in result.errors[0].message.lower()
