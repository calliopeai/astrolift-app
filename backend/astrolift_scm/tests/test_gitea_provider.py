"""Gitea provider driver — unit tests (#733).

All HTTP calls are patched out. Tests cover the happy paths and the
key error translations (auth failure → recoverable, 404 → None / idempotent,
missing api_base_url → NO_BASE_URL).
"""

from __future__ import annotations

import io
import json
import urllib.error as _urllib_error
from types import SimpleNamespace

import pytest

from astrolift_scm.providers import (
    ProviderError,
    delete_webhook,
    fetch_file,
    install_webhook,
    list_repos,
    open_pull_request,
    put_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeGiteaConn:
    """Minimal SourceConnection-shaped stand-in for gitea_pat."""

    def __init__(
        self,
        *,
        kind="gitea_pat",
        account_login="acme-org",
        api_base_url="https://gitea.example.com",
        scopes=None,
    ):
        self.kind = kind
        self.account_login = account_login
        self.api_base_url = api_base_url
        self.repo_visibility_scopes = scopes or []
        self.secret_backend_kind = "local_fernet"
        from core.secrets import encrypt_at_rest

        self.secret_ciphertext = encrypt_at_rest(b"gitea-pat-nevernetwork").backend_ref


class _FakeResp:
    """urllib context manager that returns a fixed JSON body."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body)

    def __exit__(self, *_):
        return False


def _json_resp(data) -> _FakeResp:
    return _FakeResp(json.dumps(data).encode("utf-8"))


def _http_error(code: int, body: bytes = b"") -> _urllib_error.HTTPError:
    return _urllib_error.HTTPError("https://gitea.example.com/x", code, "err", {}, io.BytesIO(body))


# ---------------------------------------------------------------------------
# list_gitea_repos
# ---------------------------------------------------------------------------

_GITEA_REPOS_PAYLOAD = {
    "data": [
        {
            "full_name": "acme-org/private-svc",
            "name": "private-svc",
            "description": "internal",
            "default_branch": "main",
            "private": True,
            "owner": {"login": "acme-org", "username": "acme-org", "type": "Organization"},
            "clone_url": "https://gitea.example.com/acme-org/private-svc.git",
            "ssh_url": "git@gitea.example.com:acme-org/private-svc.git",
            "html_url": "https://gitea.example.com/acme-org/private-svc",
            "archived": False,
            "fork": False,
            "updated_at": "2026-05-01T12:00:00Z",
        },
        {
            "full_name": "acme-org/public-api",
            "name": "public-api",
            "description": "open api",
            "default_branch": "main",
            "private": False,
            "owner": {"login": "acme-org", "username": "acme-org", "type": "Organization"},
            "clone_url": "https://gitea.example.com/acme-org/public-api.git",
            "ssh_url": "git@gitea.example.com:acme-org/public-api.git",
            "html_url": "https://gitea.example.com/acme-org/public-api",
            "archived": False,
            "fork": False,
            "updated_at": "2026-04-01T08:00:00Z",
        },
        {
            "full_name": "alice/notebook",
            "name": "notebook",
            "description": "",
            "default_branch": "main",
            "private": False,
            "owner": {"login": "alice", "username": "alice", "type": "User"},
            "clone_url": "https://gitea.example.com/alice/notebook.git",
            "ssh_url": "git@gitea.example.com:alice/notebook.git",
            "html_url": "https://gitea.example.com/alice/notebook",
            "archived": False,
            "fork": False,
            "updated_at": "2026-03-15T10:00:00Z",
        },
    ]
}


def test_list_repos_no_scopes_returns_all(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=10: _json_resp(_GITEA_REPOS_PAYLOAD),
    )
    conn = _FakeGiteaConn(scopes=[])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {
        "acme-org/private-svc",
        "acme-org/public-api",
        "alice/notebook",
    }


def test_list_repos_private_org_scope(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=10: _json_resp(_GITEA_REPOS_PAYLOAD),
    )
    conn = _FakeGiteaConn(scopes=["private_org"])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {"acme-org/private-svc"}


def test_list_repos_user_repos_scope(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=10: _json_resp(_GITEA_REPOS_PAYLOAD),
    )
    conn = _FakeGiteaConn(scopes=["user_repos"])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {"alice/notebook"}


def test_list_repos_auth_failure_is_recoverable(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(401)),
    )
    conn = _FakeGiteaConn()
    with pytest.raises(ProviderError) as exc_info:
        list(list_repos(conn))
    assert exc_info.value.code == "AUTH_FAILED"
    assert exc_info.value.recoverable is True


def test_list_repos_missing_base_url_raises_recoverable(monkeypatch):
    conn = _FakeGiteaConn(api_base_url="")
    with pytest.raises(ProviderError) as exc_info:
        list(list_repos(conn))
    assert exc_info.value.code == "NO_BASE_URL"
    assert exc_info.value.recoverable is True


# ---------------------------------------------------------------------------
# fetch_gitea_file
# ---------------------------------------------------------------------------


def test_fetch_file_returns_content(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=10: _FakeResp(b'name = "demo"\n'),
    )
    conn = _FakeGiteaConn()
    result = fetch_file(conn, repo_full_name="acme-org/private-svc", path="config.toml", ref="main")
    assert result == 'name = "demo"\n'


def test_fetch_file_returns_none_on_404(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(404)),
    )
    conn = _FakeGiteaConn()
    result = fetch_file(conn, repo_full_name="acme-org/private-svc", path="missing.toml", ref="main")
    assert result is None


def test_fetch_file_auth_failure_recoverable(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(403)),
    )
    conn = _FakeGiteaConn()
    with pytest.raises(ProviderError) as exc_info:
        fetch_file(conn, repo_full_name="acme-org/private-svc", path="x.toml", ref="main")
    assert exc_info.value.code == "AUTH_FAILED"
    assert exc_info.value.recoverable is True


# ---------------------------------------------------------------------------
# put_gitea_file
# ---------------------------------------------------------------------------

_PUT_RESPONSE = {
    "commit": {
        "sha": "abc123def456",
        "html_url": "https://gitea.example.com/acme-org/private-svc/commit/abc123",
    },
    "content": {
        "path": "workflow.yml",
        "html_url": "https://gitea.example.com/acme-org/private-svc/src/branch/main/workflow.yml",
    },
}


def test_put_file_create_new(monkeypatch):
    """When the file doesn't exist (probe returns 404) it is created."""
    calls = []

    def _fake_urlopen(req, timeout=10):
        calls.append(req.get_method())
        if req.get_method() == "GET":
            raise _http_error(404)
        return _json_resp(_PUT_RESPONSE)

    monkeypatch.setattr("astrolift_scm.providers.gitea.urllib.request.urlopen", _fake_urlopen)
    conn = _FakeGiteaConn()
    result = put_file(
        conn,
        repo_full_name="acme-org/private-svc",
        path="workflow.yml",
        branch="main",
        content="# ci\n",
        commit_message="add workflow",
    )
    assert result.commit_sha == "abc123def456"
    assert calls == ["GET", "PUT"]


def test_put_file_update_existing(monkeypatch):
    """When the probe finds an existing blob, its SHA is forwarded."""
    existing_blob = {"sha": "oldblobsha", "path": "workflow.yml"}
    put_body_seen: list[bytes] = []

    def _fake_urlopen(req, timeout=10):
        if req.get_method() == "GET":
            return _json_resp(existing_blob)
        put_body_seen.append(req.data)
        return _json_resp(_PUT_RESPONSE)

    monkeypatch.setattr("astrolift_scm.providers.gitea.urllib.request.urlopen", _fake_urlopen)
    conn = _FakeGiteaConn()
    result = put_file(
        conn,
        repo_full_name="acme-org/private-svc",
        path="workflow.yml",
        branch="main",
        content="# updated\n",
        commit_message="update workflow",
    )
    assert result.commit_sha == "abc123def456"
    body = json.loads(put_body_seen[0])
    assert body["sha"] == "oldblobsha"


# ---------------------------------------------------------------------------
# install_gitea_webhook
# ---------------------------------------------------------------------------


def test_install_webhook_success(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=15: _json_resp({"id": 42, "type": "gitea"}),
    )
    conn = _FakeGiteaConn()
    result = install_webhook(
        conn,
        repo_full_name="acme-org/private-svc",
        target_url="https://astrolift.example.com/webhooks/scm",
        secret="s3cr3t",
    )
    assert result.hook_id == "42"
    assert result.webhook_url == "https://astrolift.example.com/webhooks/scm"


def test_install_webhook_already_exists_recoverable(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(
            _http_error(422, b'{"message": "webhook url already exists"}')
        ),
    )
    conn = _FakeGiteaConn()
    with pytest.raises(ProviderError) as exc_info:
        install_webhook(
            conn,
            repo_full_name="acme-org/private-svc",
            target_url="https://astrolift.example.com/webhooks/scm",
            secret="s3cr3t",
        )
    assert exc_info.value.code == "ALREADY_EXISTS"
    assert exc_info.value.recoverable is True


# ---------------------------------------------------------------------------
# open_gitea_pull_request
# ---------------------------------------------------------------------------


def test_open_pull_request_success(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=15: _json_resp(
            {"html_url": "https://gitea.example.com/acme-org/private-svc/pulls/7", "number": 7}
        ),
    )
    conn = _FakeGiteaConn()
    result = open_pull_request(
        conn,
        repo_full_name="acme-org/private-svc",
        head_branch="feature/x",
        base_branch="main",
        title="Add feature X",
        body="Details here.",
    )
    assert result.url == "https://gitea.example.com/acme-org/private-svc/pulls/7"
    assert result.number == "7"
    assert result.head_branch == "feature/x"
    assert result.base_branch == "main"


def test_open_pull_request_already_exists_recoverable(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(
            _http_error(409, b'{"message": "pull request already exists"}')
        ),
    )
    conn = _FakeGiteaConn()
    with pytest.raises(ProviderError) as exc_info:
        open_pull_request(
            conn,
            repo_full_name="acme-org/private-svc",
            head_branch="feature/x",
            base_branch="main",
            title="Add feature X",
            body="",
        )
    assert exc_info.value.code == "ALREADY_EXISTS"
    assert exc_info.value.recoverable is True


# ---------------------------------------------------------------------------
# delete_gitea_webhook
# ---------------------------------------------------------------------------


def test_delete_webhook_success(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=10: _FakeResp(b"", 204),
    )
    conn = _FakeGiteaConn()
    delete_webhook(conn, repo_full_name="acme-org/private-svc", hook_id="42")


def test_delete_webhook_404_idempotent(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(404)),
    )
    conn = _FakeGiteaConn()
    delete_webhook(conn, repo_full_name="acme-org/private-svc", hook_id="99")


def test_delete_webhook_auth_failure_recoverable(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.gitea.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(403)),
    )
    conn = _FakeGiteaConn()
    with pytest.raises(ProviderError) as exc_info:
        delete_webhook(conn, repo_full_name="acme-org/private-svc", hook_id="42")
    assert exc_info.value.code == "AUTH_FAILED"
    assert exc_info.value.recoverable is True


# ---------------------------------------------------------------------------
# Dispatcher routing — unsupported kind
# ---------------------------------------------------------------------------


def test_unsupported_kind_raises_unsupported():
    conn = _FakeGiteaConn(kind="bitbucket_app_password")
    with pytest.raises(ProviderError) as exc_info:
        list(list_repos(conn))
    assert exc_info.value.code == "UNSUPPORTED"
