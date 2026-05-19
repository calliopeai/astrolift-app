"""Tests for #534 — Bitbucket and Gitea CI workflow sync.

Pure render / dispatch tests use SimpleNamespace stand-ins and do not
require DB access. Integration-path tests (_sync_bitbucket, _sync_gitea)
use scaffolded DB objects with provider calls stubbed at the module
boundary so no real HTTP is issued.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm import ci_templates
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError
from astrolift_scm.services.workflow_sync import (
    BITBUCKET_WORKFLOW_PATH,
    GITEA_WORKFLOW_PATH,
    WorkflowSyncError,
    _sync_bitbucket,
    _sync_gitea,
    render_astrolift_bitbucket_pipeline,
    render_astrolift_gitea_ci_workflow,
)
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_app(**kwargs) -> SimpleNamespace:
    """Minimal app-shaped object for pure render tests (no DB)."""
    return SimpleNamespace(
        slug=kwargs.get("slug", "my-app"),
        deploy_branch=kwargs.get("deploy_branch", "main"),
        registry_repo_uri=kwargs.get(
            "registry_repo_uri",
            "123456.dkr.ecr.us-east-1.amazonaws.com/my-app",
        ),
        push_role_ref=kwargs.get("push_role_ref", ""),
        source_kind=kwargs.get("source_kind", "bitbucket"),
        source_repo=kwargs.get("source_repo", "acme/my-app"),
    )


def _scaffold(*, source_kind: str = "bitbucket", source_repo: str = "acme/api"):
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        source_kind=source_kind,
        source_repo=source_repo,
        default_branch="main",
        deploy_branch="main",
        manifest_path="astrolift.toml",
        k8s_namespace="acme-hello-app",
        subdomain="hello-app",
        registry_repo_uri="123456.dkr.ecr.us-east-1.amazonaws.com/hello-app",
    )
    return org, app


def _bb_conn(org):
    encrypted = encrypt_at_rest(b"bb-token-never-hits-network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.BITBUCKET_OAUTH_USER,
        display_name="Bitbucket: acme",
        account_login="acme-workspace",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _gitea_conn(org):
    encrypted = encrypt_at_rest(b"gitea-token-never-hits-network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITEA_PAT,
        display_name="Gitea: acme",
        account_login="acme",
        api_base_url="https://gitea.example.com",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _put_result(sha="abc123"):
    return SimpleNamespace(commit_sha=sha, file_path="x", web_url="https://example.com/x")


def _pr_result(url="https://gitea.example.com/acme/api/pulls/1"):
    return SimpleNamespace(
        url=url,
        number="1",
        head_branch="astrolift/ci-workflow-hello-app",
        base_branch="main",
    )


# ---------------------------------------------------------------------------
# ci_templates constants
# ---------------------------------------------------------------------------


def test_bitbucket_workflow_path_constant():
    assert BITBUCKET_WORKFLOW_PATH == "bitbucket-pipelines.yml"


def test_gitea_workflow_path_constant():
    assert GITEA_WORKFLOW_PATH == ".gitea/workflows/astrolift-ci.yml"


# ---------------------------------------------------------------------------
# ci_templates dispatch (no DB)
# ---------------------------------------------------------------------------


def test_ci_templates_default_path_bitbucket():
    assert ci_templates.default_workflow_path_for("bitbucket") == "bitbucket-pipelines.yml"


def test_ci_templates_default_path_gitea():
    assert ci_templates.default_workflow_path_for("gitea") == ".gitea/workflows/astrolift-deploy.yml"


def test_ci_templates_render_workflow_for_bitbucket():
    out = ci_templates.render_workflow_for(source_kind="bitbucket", app_slug="acme", deploy_branch="main")
    assert "$BITBUCKET_COMMIT" in out
    assert '"acme"' in out


def test_ci_templates_render_workflow_for_gitea():
    out = ci_templates.render_workflow_for(source_kind="gitea", app_slug="acme", deploy_branch="main")
    assert "${{ github.sha }}" in out
    assert '"acme"' in out


# ---------------------------------------------------------------------------
# render_astrolift_bitbucket_pipeline (pure — SimpleNamespace app)
# ---------------------------------------------------------------------------


def test_render_bitbucket_pipeline_basic():
    out = render_astrolift_bitbucket_pipeline(_fake_app())
    assert "$BITBUCKET_COMMIT" in out
    assert "atlassian/default-image:4" in out
    assert "ASTROLIFT_DEPLOY_TOKEN" in out
    assert '"my-app"' in out
    assert "    main:\n" in out


def test_render_bitbucket_pipeline_custom_branch():
    out = render_astrolift_bitbucket_pipeline(_fake_app(deploy_branch="prod"))
    assert "    prod:\n" in out
    assert "    main:\n" not in out


def test_render_bitbucket_pipeline_ecr_push_steps():
    out = render_astrolift_bitbucket_pipeline(_fake_app())
    assert "docker build" in out
    assert "docker push" in out
    assert "ecr get-login-password" in out
    assert "services:\n            - docker" in out


def test_render_bitbucket_pipeline_slug_json_encoded():
    import json

    out = render_astrolift_bitbucket_pipeline(_fake_app(slug='needs"escape'))
    assert json.dumps('needs"escape') in out


# ---------------------------------------------------------------------------
# render_astrolift_gitea_ci_workflow (pure — SimpleNamespace app)
# ---------------------------------------------------------------------------


def test_render_gitea_ci_workflow_basic():
    out = render_astrolift_gitea_ci_workflow(_fake_app(source_kind="gitea"))
    assert "${{ github.sha }}" in out
    assert "actions/checkout@v4" in out
    assert "ASTROLIFT_DEPLOY_TOKEN" in out
    assert '"my-app"' in out
    assert "concurrency" in out


def test_render_gitea_ci_workflow_deploy_branch():
    out = render_astrolift_gitea_ci_workflow(_fake_app(source_kind="gitea", deploy_branch="release"))
    assert "branches: [release]" in out
    assert "branches: [main]" not in out


def test_render_gitea_ci_workflow_no_stray_paren():
    """Regression: earlier draft had a stray ) after the curl -d line."""
    out = render_astrolift_gitea_ci_workflow(_fake_app(source_kind="gitea"))
    lines = out.splitlines()
    curl_d_lines = [ln for ln in lines if "-d '" in ln]
    assert curl_d_lines, "curl -d line should be present"
    for line in curl_d_lines:
        assert not line.rstrip().endswith(")"), f"stray ) on curl -d line: {line!r}"


def test_render_gitea_ci_workflow_ecr_steps():
    out = render_astrolift_gitea_ci_workflow(_fake_app(source_kind="gitea"))
    assert "docker build" in out
    assert "docker push" in out
    assert "ecr get-login-password" in out


# ---------------------------------------------------------------------------
# _sync_bitbucket
# ---------------------------------------------------------------------------


def test_sync_bitbucket_creates_when_missing(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: None)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: _put_result("new-sha"),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_bitbucket(app)

    assert result.status == "created"
    assert result.commit_sha == "new-sha"


def test_sync_bitbucket_in_sync(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)

    rendered = render_astrolift_bitbucket_pipeline(app)
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: rendered)
    put_called = []
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: put_called.append(1) or _put_result(),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_bitbucket(app)

    assert result.status == "in_sync"
    assert not put_called


def test_sync_bitbucket_updated(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: "# old pipeline\n"
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: _put_result("updated-sha"),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_bitbucket(app)

    assert result.status == "updated"
    assert result.commit_sha == "updated-sha"


def test_sync_bitbucket_fetch_failed(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)

    def _raise(*a, **kw):
        raise ProviderError("AUTH_FAILED", "bad token", recoverable=True)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", _raise)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_bitbucket(app)

    assert result.status == "fetch_failed"
    assert "AUTH_FAILED" in result.error


def test_sync_bitbucket_put_failed(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: None)

    def _raise(*a, **kw):
        raise ProviderError("WRITE_FAILED", "branch restricted", recoverable=False)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.put_file", _raise)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_bitbucket(app)

    assert result.status == "fetch_failed"
    assert "WRITE_FAILED" in result.error


def test_sync_bitbucket_no_connection_raises():
    org, app = _scaffold(source_kind="bitbucket")

    with pytest.raises(WorkflowSyncError) as exc_info:
        with tenant_context(TenantContext(organization_id=org.id)):
            _sync_bitbucket(app)

    assert exc_info.value.code == "NO_CONNECTION"


# ---------------------------------------------------------------------------
# _sync_gitea
# ---------------------------------------------------------------------------


def test_sync_gitea_creates_unprotected(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    _gitea_conn(org)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: None)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._is_gitea_branch_protected",
        lambda *a, **kw: False,
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: _put_result("gitea-sha"),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_gitea(app)

    assert result.status == "created"
    assert result.commit_sha == "gitea-sha"


def test_sync_gitea_in_sync(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    _gitea_conn(org)

    rendered = render_astrolift_gitea_ci_workflow(app)
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: rendered)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_gitea(app)

    assert result.status == "in_sync"


def test_sync_gitea_updated_unprotected(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    _gitea_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: "# old workflow\n"
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._is_gitea_branch_protected",
        lambda *a, **kw: False,
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: _put_result("updated-gitea-sha"),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_gitea(app)

    assert result.status == "updated"
    assert result.commit_sha == "updated-gitea-sha"


def test_sync_gitea_protected_opens_pr(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    _gitea_conn(org)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: None)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._is_gitea_branch_protected",
        lambda *a, **kw: True,
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._gitea_create_branch_direct",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: _put_result(),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.open_pull_request",
        lambda *a, **kw: _pr_result("https://gitea.example.com/acme/api/pulls/7"),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_gitea(app)

    assert result.status == "pr_opened"
    assert result.pr_url == "https://gitea.example.com/acme/api/pulls/7"


def test_sync_gitea_protected_pr_uses_side_branch(monkeypatch):
    """put_file must be called with the side-branch name, not the deploy branch."""
    org, app = _scaffold(source_kind="gitea")
    _gitea_conn(org)

    captured_branch: list[str] = []

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: None)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._is_gitea_branch_protected",
        lambda *a, **kw: True,
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._gitea_create_branch_direct",
        lambda *a, **kw: None,
    )

    def _capture_put(*a, branch="", **kw):
        captured_branch.append(branch)
        return _put_result()

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.put_file", _capture_put)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.open_pull_request",
        lambda *a, **kw: _pr_result(),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        _sync_gitea(app)

    assert captured_branch == ["astrolift/ci-workflow-hello-app"]


def test_sync_gitea_fetch_failed(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    _gitea_conn(org)

    def _raise(*a, **kw):
        raise ProviderError("AUTH_FAILED", "bad gitea token", recoverable=True)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", _raise)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_gitea(app)

    assert result.status == "fetch_failed"
    assert "AUTH_FAILED" in result.error


def test_sync_gitea_no_connection_raises():
    org, app = _scaffold(source_kind="gitea")

    with pytest.raises(WorkflowSyncError) as exc_info:
        with tenant_context(TenantContext(organization_id=org.id)):
            _sync_gitea(app)

    assert exc_info.value.code == "NO_CONNECTION"
