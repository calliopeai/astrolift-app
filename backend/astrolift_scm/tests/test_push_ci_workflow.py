"""Tests for ``pushCiWorkflow`` mutation + the underlying GitHub /
GitLab ``put_file`` provider helpers.

The HTTP calls are stubbed at ``urllib.request.urlopen`` — we test
the path-coverage matrix (create vs update, GitHub vs GitLab, auth
failure mapping, permission gate) without hitting the network.
"""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
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


def _stub_github_put(monkeypatch, *, sha: str = "abcdef1234"):
    """Install a happy-path GitHub HTTP stub: existence probe 404 +
    successful PUT returning the supplied commit SHA. Returns the
    list of recorded requests so assertions can inspect them."""

    calls: list = []

    def fake_urlopen(req, timeout=10):
        calls.append(req)
        if req.get_method() == "GET":
            raise _http_404(req.full_url)
        return _http_response(
            {
                "commit": {"sha": sha},
                "content": {
                    "path": ".github/workflows/astrolift-deploy.yml",
                    "html_url": (
                        "https://github.com/acme/api/blob/main/.github/workflows/astrolift-deploy.yml"
                    ),
                },
            }
        )

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )
    return calls


def test_push_ci_workflow_creates_file_and_returns_sha(
    monkeypatch,
    permission_resolver,
):
    org, app = _scaffold()
    conn = _scaffold_github_oauth_user_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)
    calls = _stub_github_put(monkeypatch, sha="commitsha-NEW")

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(
                app_id=str(app.guid),
                connection_id=str(conn.guid),
            ),
        )

    assert result.ok, result.errors
    assert result.data.commit_sha == "commitsha-NEW"
    assert result.data.file_path == ".github/workflows/astrolift-deploy.yml"
    assert result.data.repo_url.startswith("https://github.com/acme/api/")
    # Validate the PUT body actually carried the rendered template.
    put_req = next(r for r in calls if r.get_method() == "PUT")
    put_body = json.loads(put_req.data.decode("utf-8"))
    import base64

    rendered = base64.b64decode(put_body["content"]).decode("utf-8")
    assert "astrolift-deploy" in rendered.lower() or "astrolift deploy" in rendered.lower()
    assert "hello-app" in rendered
    assert "ASTROLIFT_DEPLOY_TOKEN" in rendered


def test_push_ci_workflow_uses_existing_sha_on_update(
    monkeypatch,
    permission_resolver,
):
    """File-exists path: the mutation should still succeed, producing
    a new commit SHA for the in-place update."""
    org, app = _scaffold()
    conn = _scaffold_github_oauth_user_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    captured_body: dict = {}

    def fake_urlopen(req, timeout=10):
        if req.get_method() == "GET":
            return _http_response({"sha": "old-blob-sha", "type": "file"})
        captured_body.update(json.loads(req.data.decode("utf-8")))
        return _http_response(
            {
                "commit": {"sha": "rotated-commit"},
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

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(
                app_id=str(app.guid),
                connection_id=str(conn.guid),
                commit_message="Re-push deploy workflow",
            ),
        )

    assert result.ok, result.errors
    assert result.data.commit_sha == "rotated-commit"
    assert captured_body.get("sha") == "old-blob-sha"
    assert captured_body.get("message") == "Re-push deploy workflow"


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


def test_push_ci_workflow_rejects_oauth_app_config(
    monkeypatch,
    permission_resolver,
):
    """OAuth-app config rows (no account_login) can't drive a write —
    the OAuth dance hasn't produced a user token yet."""
    org, app = _scaffold()
    encrypted = encrypt_at_rest(b"client-secret-not-a-token")
    conn = SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_APP,
        display_name="GitHub OAuth app",
        oauth_client_id="Iv1.abc",
        oauth_redirect_uri="https://x/y",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(
                app_id=str(app.guid),
                connection_id=str(conn.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "oauth" in result.errors[0].message.lower()


def test_push_ci_workflow_rejects_host_mismatch(
    monkeypatch,
    permission_resolver,
):
    """A GitHub app paired with a GitLab connection fails fast at the
    resolver — we don't even try to dispatch."""
    org, app = _scaffold(source_kind="github", source_repo="acme/api")
    conn = _scaffold_gitlab_oauth_user_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(
                app_id=str(app.guid),
                connection_id=str(conn.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "connectionId"


def test_push_ci_workflow_rejects_orphaned_connection(
    monkeypatch,
    permission_resolver,
):
    """The upstream revoked / uninstalled this connection — we refuse
    to mint or use a token against it."""
    org, app = _scaffold()
    conn = _scaffold_github_oauth_user_conn(org)
    conn.is_orphaned = True
    conn.save(update_fields=["is_orphaned"])
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ScmMutation().push_ci_workflow(
            _info(),
            input=PushCiWorkflowInput(
                app_id=str(app.guid),
                connection_id=str(conn.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "orphan" in result.errors[0].message.lower()


def test_push_ci_workflow_maps_github_auth_failure_to_envelope(
    monkeypatch,
    permission_resolver,
):
    """A 401 from GitHub becomes a clean AUTH_FAILED envelope on
    ``connectionId`` rather than raising into the GraphQL error
    channel."""
    org, app = _scaffold()
    conn = _scaffold_github_oauth_user_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    def fake_urlopen(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad token"))

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
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
    assert result.errors[0].code == "AUTH_FAILED"
    assert result.errors[0].field == "connectionId"


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


def test_push_ci_workflow_renders_app_install_with_bearer(
    monkeypatch,
    permission_resolver,
):
    """github_app_install connections use Bearer auth (commit
    attribution = the App's bot) rather than the user-token shape."""
    org, app = _scaffold()
    encrypted = encrypt_at_rest(b"-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")
    conn = SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name="GitHub App: acme",
        account_login="acme",
        installation_id="12345",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )
    permission_resolver.grant(Permission.APP_UPDATE)

    # Stub the App-installation token mint so we don't actually JWT-sign.
    # The driver imports ``installation_token`` lazily from
    # ``astrolift_scm.providers.github_app`` inside ``_token()``, so the
    # patch target is the *source* module rather than ``github``.
    with patch(
        "astrolift_scm.providers.github_app.installation_token",
        return_value="ghs_fake_install_token",
    ):
        captured_auth: list[str] = []

        def fake_urlopen(req, timeout=10):
            captured_auth.append(req.get_header("Authorization") or "")
            if req.get_method() == "GET":
                raise _http_404(req.full_url)
            return _http_response(
                {
                    "commit": {"sha": "bot-commit"},
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

        with _ctx(org):
            result = ScmMutation().push_ci_workflow(
                _info(),
                input=PushCiWorkflowInput(
                    app_id=str(app.guid),
                    connection_id=str(conn.guid),
                ),
            )

    assert result.ok, result.errors
    assert result.data.commit_sha == "bot-commit"
    # The PUT call must use Bearer (App install), not "token" (user PAT).
    put_auths = [a for a in captured_auth if a]
    assert any(a.startswith("Bearer ") for a in put_auths)
