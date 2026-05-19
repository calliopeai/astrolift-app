"""Tests for #532 — GitLab / Bitbucket / Gitea workflow dispatch.

HTTP calls are stubbed at urllib.request.urlopen. Tests cover the happy
path, auth failure, workflow-file-missing detection, network errors, and
the dispatcher routing for each host.
"""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.workflows import (
    WorkflowDispatchError,
    WorkflowDispatchResult,
    _dispatch_bitbucket_pipeline,
    _dispatch_gitea_workflow,
    _dispatch_gitlab_pipeline,
    dispatch_astrolift_ci_workflow,
)
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.com/x", code, "err", {}, io.BytesIO(body))


def _url_error(reason: str = "connection refused") -> urllib.error.URLError:
    return urllib.error.URLError(reason)


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body)

    def __exit__(self, *_):
        return False

    def read(self):
        return self._body


def _json_resp(data) -> _FakeResp:
    return _FakeResp(json.dumps(data).encode())


def _scaffold(*, source_kind: str, source_repo: str = "acme/api"):
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
    )
    return org, app


def _gitlab_conn(org):
    encrypted = encrypt_at_rest(b"glat-test-token-never-network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITLAB_OAUTH_USER,
        display_name="GitLab: acme",
        account_login="acme",
        api_base_url="https://gitlab.example.com",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _bb_conn(org):
    encrypted = encrypt_at_rest(b"bb-token-never-network")
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
    encrypted = encrypt_at_rest(b"gitea-token-never-network")
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


# ---------------------------------------------------------------------------
# _dispatch_gitlab_pipeline
# ---------------------------------------------------------------------------


def test_gitlab_dispatch_success(monkeypatch):
    org, app = _scaffold(source_kind="gitlab")
    conn = _gitlab_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: _json_resp(
            {"id": 42, "web_url": "https://gitlab.example.com/acme/api/-/pipelines/42"}
        ),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_gitlab_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is True
    assert "pipelines/42" in result.run_url


def test_gitlab_dispatch_posts_correct_body(monkeypatch):
    org, app = _scaffold(source_kind="gitlab")
    conn = _gitlab_conn(org)
    captured: dict = {}

    def _fake(req, timeout=15):
        captured["body"] = json.loads(req.data)
        captured["url"] = req.full_url
        return _json_resp({"id": 1, "web_url": "https://gitlab.example.com/acme/api/-/pipelines/1"})

    monkeypatch.setattr("astrolift_scm.services.workflows.urllib.request.urlopen", _fake)

    with tenant_context(TenantContext(organization_id=org.id)):
        _dispatch_gitlab_pipeline(conn, repo_full_name="acme/api", branch="prod")

    assert captured["body"]["ref"] == "prod"
    assert "acme%2Fapi" in captured["url"] or "acme/api" in captured["url"]


def test_gitlab_dispatch_auth_failure(monkeypatch):
    org, app = _scaffold(source_kind="gitlab")
    conn = _gitlab_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(_http_error(401)),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_gitlab_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is False
    assert result.error_code == "AUTH_FAILED"


def test_gitlab_dispatch_not_found(monkeypatch):
    org, app = _scaffold(source_kind="gitlab")
    conn = _gitlab_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(_http_error(404)),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_gitlab_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is False
    assert result.error_code == "NOT_FOUND"


def test_gitlab_dispatch_config_error_maps_to_workflow_missing(monkeypatch):
    org, app = _scaffold(source_kind="gitlab")
    conn = _gitlab_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(
            _http_error(400, b'{"message":{"base":["Missing ci config file"]}}')
        ),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_gitlab_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is False
    assert result.error_code == "WORKFLOW_FILE_MISSING"


def test_gitlab_dispatch_network_error(monkeypatch):
    org, app = _scaffold(source_kind="gitlab")
    conn = _gitlab_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(_url_error("connection refused")),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_gitlab_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is False
    assert result.error_code == "NETWORK"


def test_gitlab_dispatch_fallback_run_url_when_missing_from_response(monkeypatch):
    """If the API response omits web_url, fall back to the pipelines list URL."""
    org, app = _scaffold(source_kind="gitlab")
    conn = _gitlab_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: _json_resp({"id": 99}),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_gitlab_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is True
    assert "pipelines" in result.run_url


# ---------------------------------------------------------------------------
# _dispatch_bitbucket_pipeline
# ---------------------------------------------------------------------------


def test_bitbucket_dispatch_success(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    conn = _bb_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: _json_resp({"build_number": 17, "uuid": "{abc-123}"}),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_bitbucket_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is True
    assert "/pipelines/17" in result.run_url


def test_bitbucket_dispatch_posts_correct_body(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    conn = _bb_conn(org)
    captured: dict = {}

    def _fake(req, timeout=15):
        captured["body"] = json.loads(req.data)
        captured["url"] = req.full_url
        return _json_resp({"build_number": 1})

    monkeypatch.setattr("astrolift_scm.services.workflows.urllib.request.urlopen", _fake)

    with tenant_context(TenantContext(organization_id=org.id)):
        _dispatch_bitbucket_pipeline(conn, repo_full_name="acme/api", branch="staging")

    target = captured["body"]["target"]
    assert target["ref_name"] == "staging"
    assert target["ref_type"] == "branch"
    assert target["type"] == "pipeline_ref_target"
    assert "acme/api" in captured["url"]


def test_bitbucket_dispatch_auth_failure(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    conn = _bb_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(_http_error(401)),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_bitbucket_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is False
    assert result.error_code == "AUTH_FAILED"


def test_bitbucket_dispatch_pipeline_not_enabled(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    conn = _bb_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(
            _http_error(404, b'{"error":{"message":"Repo does not have Pipelines enabled"}}')
        ),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_bitbucket_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is False
    assert result.error_code == "WORKFLOW_FILE_MISSING"


def test_bitbucket_dispatch_generic_not_found(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    conn = _bb_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(
            _http_error(404, b'{"type":"error","error":{"message":"repository does not exist"}}')
        ),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_bitbucket_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is False
    assert result.error_code == "NOT_FOUND"


def test_bitbucket_dispatch_network_error(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    conn = _bb_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(_url_error("timed out")),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_bitbucket_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is False
    assert result.error_code == "NETWORK"


def test_bitbucket_dispatch_run_url_falls_back_without_build_number(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    conn = _bb_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: _json_resp({"uuid": "{abc-123}"}),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_bitbucket_pipeline(conn, repo_full_name="acme/api", branch="main")

    assert result.ok is True
    assert result.run_url.endswith("/pipelines")


# ---------------------------------------------------------------------------
# _dispatch_gitea_workflow
# ---------------------------------------------------------------------------


def test_gitea_dispatch_success(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    conn = _gitea_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: _FakeResp(b""),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_gitea_workflow(
            conn,
            repo_full_name="acme/api",
            workflow_path=".gitea/workflows/astrolift-ci.yml",
            branch="main",
        )

    assert result.ok is True
    assert "actions" in result.run_url
    assert "gitea.example.com" in result.run_url


def test_gitea_dispatch_posts_correct_body(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    conn = _gitea_conn(org)
    captured: dict = {}

    def _fake(req, timeout=15):
        captured["body"] = json.loads(req.data)
        captured["url"] = req.full_url
        return _FakeResp(b"")

    monkeypatch.setattr("astrolift_scm.services.workflows.urllib.request.urlopen", _fake)

    with tenant_context(TenantContext(organization_id=org.id)):
        _dispatch_gitea_workflow(
            conn,
            repo_full_name="acme/api",
            workflow_path=".gitea/workflows/astrolift-ci.yml",
            branch="develop",
        )

    assert captured["body"]["ref"] == "develop"
    assert "astrolift-ci.yml" in captured["url"]
    assert "dispatches" in captured["url"]


def test_gitea_dispatch_auth_failure(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    conn = _gitea_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(_http_error(403)),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_gitea_workflow(
            conn,
            repo_full_name="acme/api",
            workflow_path=".gitea/workflows/astrolift-ci.yml",
            branch="main",
        )

    assert result.ok is False
    assert result.error_code == "AUTH_FAILED"


def test_gitea_dispatch_workflow_not_found(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    conn = _gitea_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(_http_error(404)),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_gitea_workflow(
            conn,
            repo_full_name="acme/api",
            workflow_path=".gitea/workflows/astrolift-ci.yml",
            branch="main",
        )

    assert result.ok is False
    assert result.error_code == "WORKFLOW_FILE_MISSING"


def test_gitea_dispatch_network_error(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    conn = _gitea_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflows.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(_url_error("timed out")),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _dispatch_gitea_workflow(
            conn,
            repo_full_name="acme/api",
            workflow_path=".gitea/workflows/astrolift-ci.yml",
            branch="main",
        )

    assert result.ok is False
    assert result.error_code == "NETWORK"


# ---------------------------------------------------------------------------
# dispatch_astrolift_ci_workflow — routing + invariants
# ---------------------------------------------------------------------------


def test_dispatcher_routes_to_gitlab(monkeypatch):
    org, app = _scaffold(source_kind="gitlab")
    _gitlab_conn(org)

    dispatched: list[str] = []

    def _fake_gitlab(conn, *, repo_full_name, branch):
        dispatched.append("gitlab")
        return WorkflowDispatchResult(ok=True, run_url="https://gitlab.example.com/acme/api/-/pipelines")

    monkeypatch.setattr("astrolift_scm.services.workflows._dispatch_gitlab_pipeline", _fake_gitlab)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = dispatch_astrolift_ci_workflow(app)

    assert result.ok is True
    assert dispatched == ["gitlab"]


def test_dispatcher_routes_to_bitbucket(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)

    dispatched: list[str] = []

    def _fake_bb(conn, *, repo_full_name, branch):
        dispatched.append("bitbucket")
        return WorkflowDispatchResult(ok=True, run_url="https://bitbucket.org/acme/api/pipelines")

    monkeypatch.setattr("astrolift_scm.services.workflows._dispatch_bitbucket_pipeline", _fake_bb)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = dispatch_astrolift_ci_workflow(app)

    assert result.ok is True
    assert dispatched == ["bitbucket"]


def test_dispatcher_routes_to_gitea(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    _gitea_conn(org)

    dispatched: list[str] = []

    def _fake_gitea(conn, *, repo_full_name, workflow_path, branch):
        dispatched.append("gitea")
        return WorkflowDispatchResult(ok=True, run_url="https://gitea.example.com/acme/api/actions")

    monkeypatch.setattr("astrolift_scm.services.workflows._dispatch_gitea_workflow", _fake_gitea)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = dispatch_astrolift_ci_workflow(app)

    assert result.ok is True
    assert dispatched == ["gitea"]


def test_dispatcher_raises_for_unsupported_source_kind():
    org, app = _scaffold(source_kind="git_url")

    with pytest.raises(WorkflowDispatchError) as exc_info:
        with tenant_context(TenantContext(organization_id=org.id)):
            dispatch_astrolift_ci_workflow(app)

    assert exc_info.value.code == "UNSUPPORTED_SOURCE"


def test_dispatcher_raises_no_connection_gitlab():
    org, app = _scaffold(source_kind="gitlab")

    with pytest.raises(WorkflowDispatchError) as exc_info:
        with tenant_context(TenantContext(organization_id=org.id)):
            dispatch_astrolift_ci_workflow(app)

    assert exc_info.value.code == "NO_CONNECTION"


def test_dispatcher_raises_no_connection_bitbucket():
    org, app = _scaffold(source_kind="bitbucket")

    with pytest.raises(WorkflowDispatchError) as exc_info:
        with tenant_context(TenantContext(organization_id=org.id)):
            dispatch_astrolift_ci_workflow(app)

    assert exc_info.value.code == "NO_CONNECTION"


def test_dispatcher_raises_no_connection_gitea():
    org, app = _scaffold(source_kind="gitea")

    with pytest.raises(WorkflowDispatchError) as exc_info:
        with tenant_context(TenantContext(organization_id=org.id)):
            dispatch_astrolift_ci_workflow(app)

    assert exc_info.value.code == "NO_CONNECTION"


def test_dispatcher_raises_no_source_repo():
    org, app = _scaffold(source_kind="gitlab", source_repo="acme/api")
    app.source_repo = ""

    with pytest.raises(WorkflowDispatchError) as exc_info:
        with tenant_context(TenantContext(organization_id=org.id)):
            dispatch_astrolift_ci_workflow(app)

    assert exc_info.value.code == "NO_SOURCE_REPO"


def test_dispatcher_gitea_uses_default_workflow_path(monkeypatch):
    """When workflow_path is None, dispatcher should pass the Gitea default path."""
    org, app = _scaffold(source_kind="gitea")
    _gitea_conn(org)

    captured_path: list[str] = []

    def _fake_gitea(conn, *, repo_full_name, workflow_path, branch):
        captured_path.append(workflow_path)
        return WorkflowDispatchResult(ok=True, run_url="https://gitea.example.com/acme/api/actions")

    monkeypatch.setattr("astrolift_scm.services.workflows._dispatch_gitea_workflow", _fake_gitea)

    with tenant_context(TenantContext(organization_id=org.id)):
        dispatch_astrolift_ci_workflow(app, workflow_path=None)

    assert captured_path[0] == ".gitea/workflows/astrolift-deploy.yml"


def test_dispatcher_custom_branch_override(monkeypatch):
    """Caller-supplied branch overrides app.deploy_branch."""
    org, app = _scaffold(source_kind="gitlab")
    _gitlab_conn(org)

    captured_branch: list[str] = []

    def _fake_gitlab(conn, *, repo_full_name, branch):
        captured_branch.append(branch)
        return WorkflowDispatchResult(ok=True, run_url="https://gitlab.example.com/acme/api/-/pipelines")

    monkeypatch.setattr("astrolift_scm.services.workflows._dispatch_gitlab_pipeline", _fake_gitlab)

    with tenant_context(TenantContext(organization_id=org.id)):
        dispatch_astrolift_ci_workflow(app, branch="hotfix/123")

    assert captured_branch == ["hotfix/123"]
