"""401-vs-403 GitHub auth messages must be distinct and actionable.

A 401 (invalid/expired credential) should tell the operator to reconnect;
a 403 on a GitHub App installation (connected, but under-permissioned)
must NOT — reconnecting re-installs the same App and the 403 returns. The
right guidance there is "an org admin grants the permission and re-approves
the installation". These tests lock down the shared message builder plus
the provider / service call sites that thread an operation+permission hint.

Pure unit: GitHub's HTTP layer is stubbed with ``urllib.error.HTTPError``;
no DB and no network. Message logic is DB-independent.
"""

from __future__ import annotations

import io
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_scm.auth_errors import github_auth_error_message

OLD_MISLEADING = "Reconnect or rotate"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _PatConn:
    """A user-bearer (PAT) SourceConnection stand-in whose token decrypts
    end-to-end so the driver's ``_token`` path runs without a network."""

    def __init__(self, *, api_base_url=""):
        self.kind = "github_pat"
        self.account_login = "acme"
        self.repo_visibility_scopes = []
        self.api_base_url = api_base_url
        self.secret_backend_kind = "local_fernet"
        from core.secrets import encrypt_at_rest

        self.secret_ciphertext = encrypt_at_rest(b"ghp_never_hits_the_network").backend_ref


class _AppConn:
    """A github_app_install SourceConnection stand-in. The installation
    token mint is stubbed per-test so no JWT signing / network happens."""

    def __init__(self, *, api_base_url=""):
        self.kind = "github_app_install"
        self.account_login = "acme"
        self.repo_visibility_scopes = []
        self.api_base_url = api_base_url
        self.installation_id = "12345"
        self.oauth_client_id = "67890"
        self.secret_backend_kind = "local_fernet"
        self.secret_ciphertext = b""
        self.guid = "00000000-0000-0000-0000-000000000009"


def _http_error(code):
    def _boom(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, code, "err", {}, io.BytesIO(b"denied"))

    return _boom


def _http_error_sequence(*codes):
    """urlopen stub that raises the given HTTP codes on successive calls
    (last code repeats). Lets us simulate a repo where the App holds
    ``Contents: read`` — the pre-flight GET is a 404 (no file yet) — but
    lacks ``Contents: write`` so the PUT is the thing that 403s."""
    state = {"i": 0}

    def _boom(req, timeout=10):
        i = state["i"]
        state["i"] += 1
        code = codes[i] if i < len(codes) else codes[-1]
        raise urllib.error.HTTPError(req.full_url, code, "err", {}, io.BytesIO(b"denied"))

    return _boom


def _stub_installation_token(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token",
        lambda conn: "ghs_fake_installation_token",
    )


# ---------------------------------------------------------------------------
# The message builder itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["github_app_install", "github_pat", "github_oauth_user"])
def test_401_tells_you_to_reconnect_regardless_of_kind(kind):
    msg = github_auth_error_message(401, kind, operation="write Actions secrets", permission="Secrets: write")
    assert "401" in msg
    assert "invalid or expired" in msg
    assert "Reconnect the GitHub connection" in msg
    # A 401 is the one case where reconnect IS the fix.
    assert "admin" not in msg


def test_403_app_install_names_permission_and_reapprove():
    msg = github_auth_error_message(
        403,
        "github_app_install",
        operation="write Actions secrets",
        permission="Secrets: write",
    )
    assert "403" in msg
    assert "'Secrets: write'" in msg
    assert "write Actions secrets" in msg
    assert "admin must grant" in msg
    assert "re-approve" in msg
    # The whole point: the old, misleading guidance must be gone, and the
    # message must say reconnecting won't fix an App permission.
    assert OLD_MISLEADING not in msg
    assert "reconnecting won't help" in msg


def test_403_app_install_without_permission_is_still_actionable():
    msg = github_auth_error_message(403, "github_app_install")
    assert "403" in msg
    assert "perform this operation" in msg  # generic operation fallback
    assert "required permission" in msg
    assert "admin must grant" in msg
    assert "re-approve" in msg
    assert OLD_MISLEADING not in msg


@pytest.mark.parametrize("kind", ["github_pat", "github_oauth_user"])
def test_403_user_token_points_at_scope_not_reapproval(kind):
    msg = github_auth_error_message(403, kind, operation="write Actions secrets", permission="Secrets: write")
    assert "403" in msg
    assert "scope" in msg
    assert "'Secrets: write'" in msg
    assert "Reconnect with a token" in msg
    # A user token is not an App installation — no admin re-approval story.
    assert "re-approve" not in msg
    assert OLD_MISLEADING not in msg


# ---------------------------------------------------------------------------
# Provider write path: put_github_file threads the CI-workflow hint
# ---------------------------------------------------------------------------


def test_put_file_app_install_403_names_contents_and_workflows(monkeypatch):
    """The live regression: an App with Contents: read but not write. The
    pre-flight GET 404s (no file yet), the PUT 403s, and the operator must
    see the missing GitHub App permission — not "reconnect or rotate"."""
    from astrolift_scm.providers import ProviderError, put_file
    from astrolift_scm.services.workflow_sync import (
        CI_WORKFLOW_WRITE_OPERATION,
        CI_WORKFLOW_WRITE_PERMISSION,
    )

    _stub_installation_token(monkeypatch)
    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        _http_error_sequence(404, 403),
    )

    with pytest.raises(ProviderError) as exc:
        put_file(
            _AppConn(),
            repo_full_name="acme/api",
            path=".github/workflows/astrolift-ci.yml",
            branch="main",
            content="name: ci\n",
            commit_message="chore: ci",
            operation=CI_WORKFLOW_WRITE_OPERATION,
            permission=CI_WORKFLOW_WRITE_PERMISSION,
        )

    assert exc.value.code == "AUTH_FAILED"
    assert exc.value.recoverable is True
    assert "Contents: write and Workflows: write" in exc.value.message
    assert "write the CI workflow file" in exc.value.message
    assert "re-approve" in exc.value.message
    assert OLD_MISLEADING not in exc.value.message


def test_put_file_app_install_401_still_says_reconnect(monkeypatch):
    from astrolift_scm.providers import ProviderError, put_file

    _stub_installation_token(monkeypatch)
    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        _http_error(401),
    )

    with pytest.raises(ProviderError) as exc:
        put_file(
            _AppConn(),
            repo_full_name="acme/api",
            path=".github/workflows/astrolift-ci.yml",
            branch="main",
            content="name: ci\n",
            commit_message="chore: ci",
            operation="write the CI workflow file",
            permission="Contents: write and Workflows: write",
        )

    assert exc.value.code == "AUTH_FAILED"
    assert "Reconnect the GitHub connection" in exc.value.message
    assert "re-approve" not in exc.value.message


def test_put_file_user_token_403_points_at_scope(monkeypatch):
    """A PAT (not an App) that lacks write gets scope guidance, not the
    admin/re-approve App story."""
    from astrolift_scm.providers import ProviderError, put_file

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        _http_error_sequence(404, 403),
    )

    with pytest.raises(ProviderError) as exc:
        put_file(
            _PatConn(),
            repo_full_name="acme/api",
            path=".github/workflows/astrolift-ci.yml",
            branch="main",
            content="name: ci\n",
            commit_message="chore: ci",
            operation="write the CI workflow file",
            permission="Contents: write and Workflows: write",
        )

    assert exc.value.code == "AUTH_FAILED"
    assert "scope" in exc.value.message
    assert "Contents: write and Workflows: write" in exc.value.message
    assert "re-approve" not in exc.value.message


def test_generic_provider_site_splits_401_403(monkeypatch):
    """A generic call site (list_repos) carries no operation hint, but must
    still split 401 vs 403 and give the App the admin/re-approve story."""
    from astrolift_scm.providers import ProviderError, list_repos

    _stub_installation_token(monkeypatch)

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        _http_error(403),
    )
    with pytest.raises(ProviderError) as exc:
        list(list_repos(_AppConn()))
    assert exc.value.code == "AUTH_FAILED"
    assert "perform this operation" in exc.value.message
    assert "admin must grant" in exc.value.message
    assert OLD_MISLEADING not in exc.value.message

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        _http_error(401),
    )
    with pytest.raises(ProviderError) as exc:
        list(list_repos(_AppConn()))
    assert "Reconnect the GitHub connection" in exc.value.message


# ---------------------------------------------------------------------------
# Secrets service call sites (Secrets: write)
# ---------------------------------------------------------------------------


def test_put_repo_secret_app_install_403_names_secrets_write(monkeypatch):
    from astrolift_scm.providers.github import GithubProviderError
    from astrolift_scm.services.secrets import _put_repo_secret

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.urllib.request.urlopen",
        _http_error(403),
    )
    with pytest.raises(GithubProviderError) as exc:
        _put_repo_secret(
            token="x",
            base="https://api.github.com",
            repo_full_name="acme/api",
            name="ASTROLIFT_DEPLOY_TOKEN",
            sealed_b64="c2VhbGVk",
            key_id="key-1",
            token_scheme="Bearer",
            connection_kind="github_app_install",
        )
    assert exc.value.code == "AUTH_FAILED"
    assert "'Secrets: write'" in exc.value.message
    assert "ASTROLIFT_DEPLOY_TOKEN" in exc.value.message
    assert "admin must grant" in exc.value.message
    assert "re-approve" in exc.value.message
    assert OLD_MISLEADING not in exc.value.message


def test_put_repo_secret_app_install_401_says_reconnect(monkeypatch):
    from astrolift_scm.providers.github import GithubProviderError
    from astrolift_scm.services.secrets import _put_repo_secret

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.urllib.request.urlopen",
        _http_error(401),
    )
    with pytest.raises(GithubProviderError) as exc:
        _put_repo_secret(
            token="x",
            base="https://api.github.com",
            repo_full_name="acme/api",
            name="ASTROLIFT_DEPLOY_TOKEN",
            sealed_b64="c2VhbGVk",
            key_id="key-1",
            token_scheme="Bearer",
            connection_kind="github_app_install",
        )
    assert "Reconnect the GitHub connection" in exc.value.message
    assert "re-approve" not in exc.value.message


def test_fetch_repo_public_key_app_install_403_names_secrets_write(monkeypatch):
    """The public-key fetch is the first write-flow call to 403 when the App
    has no Secrets permission at all (the live case)."""
    from astrolift_scm.providers.github import GithubProviderError
    from astrolift_scm.services.secrets import _fetch_repo_public_key

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.urllib.request.urlopen",
        _http_error(403),
    )
    with pytest.raises(GithubProviderError) as exc:
        _fetch_repo_public_key(
            token="x",
            base="https://api.github.com",
            repo_full_name="acme/api",
            token_scheme="Bearer",
            connection_kind="github_app_install",
        )
    assert "'Secrets: write'" in exc.value.message
    assert "re-approve" in exc.value.message
    assert OLD_MISLEADING not in exc.value.message


def test_put_repo_secret_user_token_403_points_at_scope(monkeypatch):
    from astrolift_scm.providers.github import GithubProviderError
    from astrolift_scm.services.secrets import _put_repo_secret

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.urllib.request.urlopen",
        _http_error(403),
    )
    with pytest.raises(GithubProviderError) as exc:
        _put_repo_secret(
            token="x",
            base="https://api.github.com",
            repo_full_name="acme/api",
            name="ASTROLIFT_DEPLOY_TOKEN",
            sealed_b64="c2VhbGVk",
            key_id="key-1",
            token_scheme="token",
            connection_kind="github_pat",
        )
    assert "scope" in exc.value.message
    assert "'Secrets: write'" in exc.value.message
    assert "re-approve" not in exc.value.message


# ---------------------------------------------------------------------------
# Workflow-sync service threads the CI hint into put_file
# ---------------------------------------------------------------------------


def test_sync_github_threads_ci_workflow_permission_hint(monkeypatch):
    """_sync_github must hand put_file the Contents/Workflows hint so a 403
    from an under-permitted App is actionable. Provider + connection picker
    are stubbed; we assert the exact kwargs cross the boundary."""
    from astrolift_scm.services import workflow_sync as ws

    captured: dict = {}

    def _capture_put(connection, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(commit_sha="deadbeef", file_path=ws.WORKFLOW_PATH, web_url="https://x")

    monkeypatch.setattr(ws, "put_file", _capture_put)
    monkeypatch.setattr(ws, "fetch_file", lambda *a, **k: None)
    monkeypatch.setattr(ws, "_is_github_branch_protected", lambda *a, **k: False)
    monkeypatch.setattr(
        ws,
        "_pick_source_connection",
        lambda app: SimpleNamespace(kind="github_app_install", api_base_url=""),
    )
    monkeypatch.setattr(ws, "render_astrolift_ci_workflow", lambda app: "name: ci\n")

    app = SimpleNamespace(
        guid="11111111-1111-4111-8111-111111111111",
        source_kind="github",
        source_repo="acme/api",
        deploy_branch="main",
        slug="hello",
    )
    result = ws._sync_github(app)

    assert result.status == "created"
    assert captured["operation"] == ws.CI_WORKFLOW_WRITE_OPERATION
    assert captured["permission"] == ws.CI_WORKFLOW_WRITE_PERMISSION
    assert captured["permission"] == "Contents: write and Workflows: write"


# ---------------------------------------------------------------------------
# Missing ``workflow`` scope refusal (#1542)
# ---------------------------------------------------------------------------


def _http_error_sequence_with_body(*steps):
    """Like ``_http_error_sequence`` but each step is ``(code, body)`` so a
    test can present GitHub's actual refusal text, which the provider
    detects by body rather than status code."""
    state = {"i": 0}

    def _boom(req, timeout=10):
        i = state["i"]
        state["i"] += 1
        code, body = steps[i] if i < len(steps) else steps[-1]
        raise urllib.error.HTTPError(req.full_url, code, "err", {}, io.BytesIO(body))

    return _boom


_WORKFLOW_REFUSAL = (
    b'{"message":"refusing to allow an OAuth App to create or update workflow '
    b'`.github/workflows/astrolift-ci.yml` without `workflow` scope"}'
)


@pytest.mark.parametrize("kind", ["github_oauth_user", "github_pat"])
def test_put_file_workflow_scope_refusal_says_reconnect_to_reissue(monkeypatch, kind):
    """GitHub's categorical workflow-scope refusal must surface as its own
    code with re-issue guidance — scopes freeze at authorization, so the
    only remedy is reconnecting, never retrying (#1542)."""
    from astrolift_scm.providers import ProviderError, put_file

    conn = _PatConn()
    conn.kind = kind
    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        _http_error_sequence_with_body((404, b"denied"), (422, _WORKFLOW_REFUSAL)),
    )

    with pytest.raises(ProviderError) as exc:
        put_file(
            conn,
            repo_full_name="acme/personal-api",
            path=".github/workflows/astrolift-ci.yml",
            branch="main",
            content="name: ci\n",
            commit_message="chore: ci",
        )

    assert exc.value.code == "MISSING_WORKFLOW_SCOPE"
    assert "Reconnect" in exc.value.message
    assert "workflow" in exc.value.message


def test_put_file_app_install_never_maps_to_workflow_scope(monkeypatch):
    """App installs have permissions, not scopes — a body that happens to
    mention scopes must still ride the 403 permission path."""
    from astrolift_scm.providers import ProviderError, put_file
    from astrolift_scm.providers.github_app import installation_token as _real  # noqa: F401

    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token",
        lambda connection: "ghs_test_token",
    )
    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        _http_error_sequence_with_body((404, b"denied"), (403, _WORKFLOW_REFUSAL)),
    )

    with pytest.raises(ProviderError) as exc:
        put_file(
            _AppConn(),
            repo_full_name="acme/api",
            path=".github/workflows/astrolift-ci.yml",
            branch="main",
            content="name: ci\n",
            commit_message="chore: ci",
            operation="write the CI workflow file",
            permission="Contents: write and Workflows: write",
        )

    assert exc.value.code == "AUTH_FAILED"
