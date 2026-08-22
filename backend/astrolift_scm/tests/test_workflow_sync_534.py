"""Tests for #534 — Bitbucket and Gitea CI workflow sync.

Pure render / dispatch tests use SimpleNamespace stand-ins and do not
require DB access. Integration-path tests (_sync_bitbucket, _sync_gitea)
use scaffolded DB objects with provider calls stubbed at the module
boundary so no real HTTP is issued.
"""

from __future__ import annotations

import re
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
    # The notify step targets the real CI deploy endpoint, slug in-path (#1220).
    assert "/api/cli/v1/apps/my-app/deploy/" in out
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


def test_render_bitbucket_pipeline_slug_lands_in_deploy_path():
    out = render_astrolift_bitbucket_pipeline(_fake_app(slug="custom-slug"))
    assert "/api/cli/v1/apps/custom-slug/deploy/" in out


# ---------------------------------------------------------------------------
# render_astrolift_gitea_ci_workflow (pure — SimpleNamespace app)
# ---------------------------------------------------------------------------


def test_render_gitea_ci_workflow_basic():
    out = render_astrolift_gitea_ci_workflow(_fake_app(source_kind="gitea"))
    assert "${{ github.sha }}" in out
    assert "actions/checkout@v4" in out
    assert "ASTROLIFT_DEPLOY_TOKEN" in out
    assert "/api/cli/v1/apps/my-app/deploy/" in out
    assert "concurrency" in out


def test_render_gitea_ci_workflow_deploy_branch():
    out = render_astrolift_gitea_ci_workflow(_fake_app(source_kind="gitea", deploy_branch="release"))
    assert "branches: [release]" in out
    assert "branches: [main]" not in out


def test_render_gitea_ci_workflow_curl_substitution_balanced():
    """The curl runs inside ``code=$(...)`` so its status can be asserted;
    the ``)`` on the -d line must close that substitution — exactly one
    opener, exactly one closer, in order."""
    out = render_astrolift_gitea_ci_workflow(_fake_app(source_kind="gitea"))
    lines = out.splitlines()
    open_lines = [i for i, ln in enumerate(lines) if "code=$(curl" in ln]
    close_lines = [i for i, ln in enumerate(lines) if ln.rstrip().endswith("}')")]
    assert len(open_lines) == 1, "expected one code=$(curl opener"
    assert len(close_lines) == 1, "expected one closing ) on the -d line"
    assert open_lines[0] < close_lines[0]


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


def _stale_stamped_baseline(app, rendered: str) -> str:
    """Record ``rendered`` as the platform's own last write, then hand back the
    same body stamped one template version back.

    This is what a template bump looks like on a file nobody has touched:
    the body hash still matches the baseline (``content_hash`` strips the
    stamp), so the state is ``template_stale`` and a direct write loses
    nothing. A repo file with NO baseline behind it is a different case --
    it is somebody else's file, and overwriting it is the bug the
    ``_has_operator_edits`` guard exists to stop.
    """
    app.ci_workflow_template_version = max(ci_templates.TEMPLATE_VERSION - 1, 0)
    app.ci_workflow_state = {
        "synced_hash": ci_templates.content_hash(rendered),
        "synced_blob_sha": ci_templates.git_blob_sha(rendered.encode("utf-8")),
        "state": "in_sync",
    }
    app.save(update_fields=["ci_workflow_template_version", "ci_workflow_state", "updated_at", "version"])
    return re.sub(
        r"template-version=\d+",
        f"template-version={max(ci_templates.TEMPLATE_VERSION - 1, 0)}",
        rendered,
        count=1,
    )


def test_sync_bitbucket_updated(monkeypatch):
    """A file the platform itself last wrote, one template behind, is updated
    in place. Nothing is lost, so there is nothing to review."""
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)
    rendered = render_astrolift_bitbucket_pipeline(app)
    repo_text = _stale_stamped_baseline(app, rendered)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: repo_text)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: _put_result("updated-sha"),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_bitbucket(app)

    assert result.status == "updated"
    assert result.commit_sha == "updated-sha"


def test_sync_bitbucket_refuses_to_overwrite_a_pipeline_it_did_not_write(monkeypatch):
    """``bitbucket-pipelines.yml`` is the HOST's conventional CI path, not an
    Astrolift-specific one, so a repo that already does CI has a real file
    there. This used to be overwritten on the first push, destroying the
    tenant's own pipeline definition, and Bitbucket has no side-branch PR
    flow wired to review a replacement -- so refuse.
    """
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: "# their own pipeline\n"
    )

    def _boom(*a, **kw):
        raise AssertionError("must not overwrite a pipeline Astrolift did not write")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.put_file", _boom)

    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(WorkflowSyncError) as exc:
            _sync_bitbucket(app)

    assert exc.value.code == "WOULD_OVERWRITE_EDITS"


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
    """Same as Bitbucket's case: the platform's own file, one version behind,
    is updated in place on an unprotected branch."""
    org, app = _scaffold(source_kind="gitea")
    _gitea_conn(org)
    rendered = render_astrolift_gitea_ci_workflow(app)
    repo_text = _stale_stamped_baseline(app, rendered)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: repo_text)
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


def test_sync_gitea_hand_edited_file_goes_through_a_pr_not_over_it(monkeypatch):
    """An unprotected branch was the hazard: the write was available, so the
    edit was taken. Now the edit routes through a review PR instead."""
    org, app = _scaffold(source_kind="gitea")
    _gitea_conn(org)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: "# hand-edited\n")
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._is_gitea_branch_protected",
        lambda *a, **kw: False,
    )
    branches: list[str] = []
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: branches.append(kw.get("branch", "")) or _put_result("pr-sha"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._gitea_create_branch_direct", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.open_pull_request",
        lambda *a, **kw: SimpleNamespace(url="https://gitea.example/acme/api/pulls/3"),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = _sync_gitea(app)

    assert result.status == "pr_opened"
    assert branches and branches[0] != "main", "the write must land on a side branch"


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
