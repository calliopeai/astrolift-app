"""Tests for the ``pushCiWorkflow`` mutation + the underlying GitHub /
GitLab ``put_file`` provider helpers.

Two layers:

* the provider ``put_file`` create/update/auth matrix, stubbed at
  ``urllib.request.urlopen`` (unchanged — this is the shared write plumbing);
* the ``pushCiWorkflow`` mutation, which after #1212 DELEGATES to System B's
  ``sync_workflow_file_to_repo``. The mutation tests assert that delegation and
  the resulting single stamped System-B file per host (the collision fix), not
  the retired System-A ``astrolift-deploy.yml`` render.
"""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError, put_file
from astrolift_scm.schema.mutations import (
    PushCiWorkflowInput,
    ScmMutation,
)
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures: scaffolding + HTTP stub
# ---------------------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _scaffold(*, source_kind: str = "github", source_repo: str = "acme/api"):
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo",
    )
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
        registry_repo_uri="123456789012.dkr.ecr.us-west-2.amazonaws.com/hello-app",
    )
    return org, app


def _scaffold_github_oauth_user_conn(org, *, account_login: str = "alice") -> SourceConnection:
    """A user-OAuth connection — commit attribution would be 'alice'
    on GitHub. Encrypts a placeholder token so the driver's decrypt
    path runs through unmodified."""
    encrypted = encrypt_at_rest(b"gho_test_user_token_never_hits_the_network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name=f"GitHub: {account_login}",
        account_login=account_login,
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _scaffold_gitlab_oauth_user_conn(org, *, account_login: str = "alice") -> SourceConnection:
    encrypted = encrypt_at_rest(b"glat-test-user-token-never-hits-the-network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITLAB_OAUTH_USER,
        display_name=f"GitLab: {account_login}",
        account_login=account_login,
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


class _CMResponse:
    """urllib-style context manager that hands back a fixed JSON body
    and an optional status code (only used by the GitLab HEAD path)."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body, status=self.status)

    def __exit__(self, *_):
        return False


def _http_response(payload: dict | list | None = None, *, status: int = 200) -> _CMResponse:
    return _CMResponse(json.dumps(payload or {}).encode("utf-8"), status=status)


def _http_404(url: str = "https://example") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"missing"))


# ---------------------------------------------------------------------------
# Provider-level tests: GitHub put_file create vs update
# ---------------------------------------------------------------------------


def test_github_put_file_creates_when_missing(monkeypatch):
    """First write to a path: the existence-probe GET returns 404,
    the PUT body omits ``sha``, and the driver surfaces the new
    commit SHA."""
    org, _ = _scaffold()
    conn = _scaffold_github_oauth_user_conn(org)

    calls = []

    def fake_urlopen(req, timeout=10):
        calls.append(req)
        if req.get_method() == "GET":
            raise _http_404(req.full_url)
        # PUT: parse and return the canonical Contents API success body.
        body = json.loads(req.data.decode("utf-8"))
        assert "sha" not in body, "create path must not include sha"
        assert body["branch"] == "main"
        return _http_response(
            {
                "commit": {"sha": "deadbeefcafebabe"},
                "content": {
                    "path": ".github/workflows/astrolift-deploy.yml",
                    "html_url": "https://github.com/acme/api/blob/main/.github/workflows/astrolift-deploy.yml",
                },
            }
        )

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    result = put_file(
        conn,
        repo_full_name="acme/api",
        path=".github/workflows/astrolift-deploy.yml",
        branch="main",
        content="name: x\n",
        commit_message="hello",
    )
    assert result.commit_sha == "deadbeefcafebabe"
    assert result.file_path == ".github/workflows/astrolift-deploy.yml"
    assert result.web_url.startswith("https://github.com/acme/api/blob/main/")
    # Two HTTP calls: GET probe + PUT write.
    assert [r.get_method() for r in calls] == ["GET", "PUT"]


def test_github_put_file_updates_when_present(monkeypatch):
    """File already exists: the probe GET returns 200 with the blob
    SHA, the PUT body includes ``sha``, the driver returns the new
    commit SHA. This is the regression path for the 422 'file exists'
    failure mode the brief calls out."""
    org, _ = _scaffold()
    conn = _scaffold_github_oauth_user_conn(org)

    captured_put_body: dict = {}

    def fake_urlopen(req, timeout=10):
        if req.get_method() == "GET":
            return _http_response({"sha": "existing-blob-sha", "type": "file"})
        body = json.loads(req.data.decode("utf-8"))
        captured_put_body.update(body)
        return _http_response(
            {
                "commit": {"sha": "newcommit12345"},
                "content": {
                    "path": ".github/workflows/astrolift-deploy.yml",
                    "html_url": "https://github.com/acme/api/blob/main/.github/workflows/astrolift-deploy.yml",
                },
            }
        )

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    result = put_file(
        conn,
        repo_full_name="acme/api",
        path=".github/workflows/astrolift-deploy.yml",
        branch="main",
        content="name: y\n",
        commit_message="update",
    )
    assert result.commit_sha == "newcommit12345"
    assert captured_put_body.get("sha") == "existing-blob-sha"


def test_github_put_file_auth_failure_is_recoverable(monkeypatch):
    """A 401 from the probe GET surfaces as AUTH_FAILED + recoverable."""
    org, _ = _scaffold()
    conn = _scaffold_github_oauth_user_conn(org)

    def fake_urlopen(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad token"))

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(ProviderError) as exc:
        put_file(
            conn,
            repo_full_name="acme/api",
            path=".github/workflows/astrolift-deploy.yml",
            branch="main",
            content="x",
            commit_message="hi",
        )
    assert exc.value.code == "AUTH_FAILED"
    assert exc.value.recoverable is True


# ---------------------------------------------------------------------------
# Provider-level tests: GitLab put_file create vs update
# ---------------------------------------------------------------------------


def test_gitlab_put_file_creates_when_missing(monkeypatch):
    """First write to a path on GitLab: HEAD 404 → POST → branch GET
    for the head SHA."""
    org, _ = _scaffold(source_kind="gitlab", source_repo="acme/api")
    conn = _scaffold_gitlab_oauth_user_conn(org)

    seen_methods = []

    def fake_urlopen(req, timeout=10):
        method = req.get_method()
        seen_methods.append(method)
        if method == "HEAD":
            raise _http_404(req.full_url)
        if method == "POST":
            body = json.loads(req.data.decode("utf-8"))
            assert body["branch"] == "main"
            assert body["encoding"] == "text"
            return _http_response({"file_path": ".gitlab-ci.yml", "branch": "main"})
        # GET branch head
        return _http_response({"commit": {"id": "abc123def"}})

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        fake_urlopen,
    )

    result = put_file(
        conn,
        repo_full_name="acme/api",
        path=".gitlab-ci.yml",
        branch="main",
        content="stages: [deploy]\n",
        commit_message="add gitlab ci",
    )
    assert result.commit_sha == "abc123def"
    assert result.file_path == ".gitlab-ci.yml"
    assert "/-/blob/main/" in result.web_url
    assert seen_methods == ["HEAD", "POST", "GET"]


def test_gitlab_put_file_updates_when_present(monkeypatch):
    """HEAD 200 → PUT (not POST) → branch GET for SHA."""
    org, _ = _scaffold(source_kind="gitlab", source_repo="acme/api")
    conn = _scaffold_gitlab_oauth_user_conn(org)

    seen_methods = []

    def fake_urlopen(req, timeout=10):
        method = req.get_method()
        seen_methods.append(method)
        if method == "HEAD":
            return _http_response({})
        if method == "PUT":
            return _http_response({"file_path": ".gitlab-ci.yml", "branch": "main"})
        return _http_response({"commit": {"id": "updated-sha"}})

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        fake_urlopen,
    )

    result = put_file(
        conn,
        repo_full_name="acme/api",
        path=".gitlab-ci.yml",
        branch="main",
        content="stages: [deploy]\n",
        commit_message="update gitlab ci",
    )
    assert result.commit_sha == "updated-sha"
    assert seen_methods == ["HEAD", "PUT", "GET"]


# ---------------------------------------------------------------------------
# Mutation-level tests: push_ci_workflow
# ---------------------------------------------------------------------------


def _capture_sync_put(monkeypatch, *, source_kind: str, existing=None):
    """Stub the System-B sync's repo I/O (existing-file fetch + protection
    probe + put_file) and capture the ``put_file`` kwargs so a test can assert
    WHAT content lands at WHICH path — the proof System B (not legacy System A)
    is what pushes now."""
    captured: dict = {}
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: existing)
    probe = {
        "github": "astrolift_scm.services.workflow_sync._is_github_branch_protected",
        "gitlab": "astrolift_scm.services.workflow_sync._is_gitlab_branch_protected",
        "gitea": "astrolift_scm.services.workflow_sync._is_gitea_branch_protected",
    }.get(source_kind)
    if probe:
        monkeypatch.setattr(probe, lambda *a, **kw: False)

    def _put(*a, **kw):
        captured.update(kw)
        return SimpleNamespace(commit_sha="new-commit", file_path=kw.get("path"), web_url="https://x")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.put_file", _put)
    return captured


def test_push_ci_workflow_routes_to_sync_workflow_file_to_repo(
    monkeypatch,
    permission_resolver,
):
    """#1212 consolidation: the legacy System-A mutation now DELEGATES to
    System B's ``sync_workflow_file_to_repo`` — it doesn't render/push itself."""
    org, app = _scaffold()
    conn = _scaffold_github_oauth_user_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    seen: dict = {}

    def _spy(a, viewer_user=None, *, force_pr=False):
        seen["app_pk"] = a.pk
        seen["force_pr"] = force_pr
        return SimpleNamespace(status="created", commit_sha="deleg-sha", pr_url="", rendered_size=1, error="")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo", _spy)

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(app_id=str(app.guid), connection_id=str(conn.guid)),
        )

    assert result.ok, result.errors
    assert seen["app_pk"] == app.pk  # routed to System B with the app
    assert seen["force_pr"] is False  # a wizard push is a normal sync, not a forced PR
    assert result.data.commit_sha == "deleg-sha"
    # Canonical System-B path (astrolift-ci.yml), NOT legacy astrolift-deploy.yml.
    assert result.data.file_path == f".github/workflows/astrolift-app-{app.guid.hex}.yml"


def test_push_ci_workflow_writes_system_b_stamped_file_github(
    monkeypatch,
    permission_resolver,
):
    """End-to-end: the bytes that land are System B's STAMPED astrolift-ci.yml
    at the canonical path — legacy System A never stamped and used a different
    filename."""
    org, app = _scaffold()
    _scaffold_github_oauth_user_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)
    captured = _capture_sync_put(monkeypatch, source_kind="github", existing=None)

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(app_id=str(app.guid), connection_id="ignored-now"),
        )

    assert result.ok, result.errors
    assert captured["path"] == f".github/workflows/astrolift-app-{app.guid.hex}.yml"
    # The stamp line is System B's fingerprint — System A output carried none.
    assert "# astrolift-managed:" in captured["content"]


def test_agent_workflow_path_does_not_collide_in_monorepo(monkeypatch):
    from astrolift_scm.services.workflow_sync import sync_workflow_file_to_repo

    org, app = _scaffold(source_repo="acme/agents")
    _scaffold_github_oauth_user_conn(org)
    Workload.objects.create(
        registered_app=app,
        name="Hello agent",
        slug="hello-agent",
        kind=Workload.Kind.AGENT,
    )
    captured = _capture_sync_put(monkeypatch, source_kind="github", existing=None)

    result = sync_workflow_file_to_repo(app)

    assert result.status == "created"
    assert captured["path"] == ".github/workflows/astrolift-agent-hello-app.yml"


def test_push_ci_workflow_requires_permission(monkeypatch):
    org, app = _scaffold()
    conn = _scaffold_github_oauth_user_conn(org)
    # No grant — permission_resolver fixture omitted.

    # Stubbed in case the resolver reaches the network (it shouldn't).
    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not call network")),
    )

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(
                app_id=str(app.guid),
                connection_id=str(conn.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_push_ci_workflow_gitlab_writes_single_system_b_file(
    monkeypatch,
    permission_resolver,
):
    """#1212 collision fix: on GitLab, System A and System B both targeted
    ``.gitlab-ci.yml``. After consolidation the delegating mutation writes
    exactly ONE file — System B's stamped content — at that shared path, so the
    two renderers can no longer clobber each other."""
    org, app = _scaffold(source_kind="gitlab", source_repo="acme/api")
    _scaffold_gitlab_oauth_user_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)
    captured = _capture_sync_put(monkeypatch, source_kind="gitlab", existing=None)

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(app_id=str(app.guid), connection_id="ignored-now"),
        )

    assert result.ok, result.errors
    assert captured["path"] == ".gitlab-ci.yml"
    # System B's stamp — legacy System A's .gitlab-ci.yml was unstamped.
    assert "# astrolift-managed:" in captured["content"]


def test_push_ci_workflow_maps_fetch_failure_to_precondition(
    monkeypatch,
    permission_resolver,
):
    """A host error surfaces as System B's ``fetch_failed`` → a clean
    PRECONDITION envelope rather than raising into the GraphQL error channel."""
    org, app = _scaffold()
    _scaffold_github_oauth_user_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    def _raise(*a, **kw):
        raise ProviderError("AUTH_FAILED", "bad token")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", _raise)

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(app_id=str(app.guid), connection_id="ignored-now"),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "AUTH_FAILED" in result.errors[0].message


def test_push_ci_workflow_app_not_found(monkeypatch, permission_resolver):
    org, _ = _scaffold()
    conn = _scaffold_github_oauth_user_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(
                app_id="00000000-0000-0000-0000-000000000000",
                connection_id=str(conn.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appId"


def test_push_ci_workflow_bitbucket_writes_single_system_b_file(
    monkeypatch,
    permission_resolver,
):
    """#1212 collision fix (Bitbucket): the shared ``bitbucket-pipelines.yml``
    now carries only System B's stamped content — the legacy System-A renderer
    that also wrote that path is retired."""
    org, app = _scaffold(source_kind="bitbucket", source_repo="acme/api")
    encrypted = encrypt_at_rest(b"bb-token-never-hits-network")
    SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.BITBUCKET_OAUTH_USER,
        display_name="Bitbucket: acme",
        account_login="acme-workspace",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )
    permission_resolver.grant(Permission.APP_UPDATE)
    captured = _capture_sync_put(monkeypatch, source_kind="bitbucket", existing=None)

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(app_id=str(app.guid), connection_id="ignored-now"),
        )

    assert result.ok, result.errors
    assert captured["path"] == "bitbucket-pipelines.yml"
    assert "# astrolift-managed:" in captured["content"]
