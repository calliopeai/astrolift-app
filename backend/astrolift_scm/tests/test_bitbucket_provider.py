"""Bitbucket Cloud provider driver — unit tests (#732).

All HTTP calls are patched out. Tests cover happy paths and the key
error translations: auth failure → recoverable, 404 → None / idempotent,
missing workspace → VALIDATION.
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


class _FakeBBConn:
    """Minimal SourceConnection-shaped stand-in for bitbucket_oauth_user."""

    def __init__(
        self,
        *,
        kind="bitbucket_oauth_user",
        account_login="acme-workspace",
        scopes=None,
    ):
        self.kind = kind
        self.account_login = account_login
        self.repo_visibility_scopes = scopes or []
        self.secret_backend_kind = "local_fernet"
        from core.secrets import encrypt_at_rest

        self.secret_ciphertext = encrypt_at_rest(b"bb-oauth-token-nevernetwork").backend_ref


class _FakePATConn(_FakeBBConn):
    """bitbucket_pat connection (app password)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("kind", "bitbucket_pat")
        super().__init__(**kwargs)


class _FakeResp:
    def __init__(self, body: bytes, *, url: str = "https://api.bitbucket.org/done"):
        self._body = body
        self.url = url

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body, url=self.url)

    def __exit__(self, *_):
        return False

    def read(self):
        return self._body


def _json_resp(data, *, url: str = "https://api.bitbucket.org/done") -> _FakeResp:
    return _FakeResp(json.dumps(data).encode(), url=url)


def _http_error(code: int, body: bytes = b"") -> _urllib_error.HTTPError:
    return _urllib_error.HTTPError("https://api.bitbucket.org/x", code, "err", {}, io.BytesIO(body))


# ---------------------------------------------------------------------------
# list_bitbucket_repos
# ---------------------------------------------------------------------------

_BB_REPOS_PAYLOAD = {
    "values": [
        {
            "full_name": "acme-workspace/private-svc",
            "name": "private-svc",
            "slug": "private-svc",
            "description": "internal",
            "is_private": True,
            "workspace": {"slug": "acme-workspace"},
            "mainbranch": {"name": "main"},
            "links": {
                "html": {"href": "https://bitbucket.org/acme-workspace/private-svc"},
                "clone": [
                    {"name": "https", "href": "https://acme@bitbucket.org/acme-workspace/private-svc.git"},
                    {"name": "ssh", "href": "git@bitbucket.org:acme-workspace/private-svc.git"},
                ],
            },
            "updated_on": "2026-05-01T12:00:00+00:00",
        },
        {
            "full_name": "acme-workspace/public-api",
            "name": "public-api",
            "slug": "public-api",
            "description": "open api",
            "is_private": False,
            "workspace": {"slug": "acme-workspace"},
            "mainbranch": {"name": "main"},
            "links": {
                "html": {"href": "https://bitbucket.org/acme-workspace/public-api"},
                "clone": [],
            },
            "updated_on": "2026-04-01T08:00:00+00:00",
        },
    ]
}


def test_list_repos_no_scopes_returns_all(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=10: _json_resp(_BB_REPOS_PAYLOAD),
    )
    conn = _FakeBBConn(scopes=[])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {
        "acme-workspace/private-svc",
        "acme-workspace/public-api",
    }


def test_list_repos_private_org_scope(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=10: _json_resp(_BB_REPOS_PAYLOAD),
    )
    conn = _FakeBBConn(scopes=["private_org"])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {"acme-workspace/private-svc"}


def test_list_repos_public_org_scope(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=10: _json_resp(_BB_REPOS_PAYLOAD),
    )
    conn = _FakeBBConn(scopes=["public_org"])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {"acme-workspace/public-api"}


def test_list_repos_auth_failure_is_recoverable(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(401)),
    )
    conn = _FakeBBConn()
    with pytest.raises(ProviderError) as exc_info:
        list(list_repos(conn))
    assert exc_info.value.code == "AUTH_FAILED"
    assert exc_info.value.recoverable is True


def test_list_repos_missing_workspace_raises_validation(monkeypatch):
    conn = _FakeBBConn(account_login="")
    with pytest.raises(ProviderError) as exc_info:
        list(list_repos(conn))
    assert exc_info.value.code == "VALIDATION"
    assert exc_info.value.recoverable is True


def test_list_repos_pat_auth_uses_basic_header(monkeypatch):
    """bitbucket_pat must emit a Basic auth header, not Bearer."""
    import base64

    captured: dict = {}

    def _fake_urlopen(req, timeout=10):
        captured["auth"] = req.get_header("Authorization")
        return _json_resp(_BB_REPOS_PAYLOAD)

    monkeypatch.setattr("astrolift_scm.providers.bitbucket.urllib.request.urlopen", _fake_urlopen)
    conn = _FakePATConn()
    list(list_repos(conn))
    assert captured["auth"].startswith("Basic ")
    decoded = base64.b64decode(captured["auth"][6:]).decode()
    username, _ = decoded.split(":", 1)
    assert username == "acme-workspace"


# ---------------------------------------------------------------------------
# fetch_bitbucket_file
# ---------------------------------------------------------------------------


def test_fetch_file_returns_content(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=10: _FakeResp(b'name = "demo"\n'),
    )
    conn = _FakeBBConn()
    result = fetch_file(conn, repo_full_name="acme-workspace/private-svc", path="config.toml", ref="main")
    assert result == 'name = "demo"\n'


def test_fetch_file_returns_none_on_404(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(404)),
    )
    conn = _FakeBBConn()
    result = fetch_file(conn, repo_full_name="acme-workspace/private-svc", path="missing.toml", ref="main")
    assert result is None


def test_fetch_file_auth_failure_recoverable(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(403)),
    )
    conn = _FakeBBConn()
    with pytest.raises(ProviderError) as exc_info:
        fetch_file(conn, repo_full_name="acme-workspace/private-svc", path="x.toml", ref="main")
    assert exc_info.value.code == "AUTH_FAILED"
    assert exc_info.value.recoverable is True


# ---------------------------------------------------------------------------
# put_bitbucket_file
# ---------------------------------------------------------------------------


def test_put_file_posts_multipart(monkeypatch):
    """put_file must POST with multipart/form-data and return a result."""
    captured: dict = {}

    class _FakeOpener:
        def open(self, req, timeout=15):
            captured["method"] = req.get_method()
            captured["content_type"] = req.get_header("Content-type")
            captured["data"] = req.data
            resp = SimpleNamespace(read=lambda: b"", url="https://bitbucket.org/x/commit/abc123sha")
            return _MockCtx(resp)

    class _MockCtx:
        def __init__(self, val):
            self._val = val

        def __enter__(self):
            return self._val

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.build_opener",
        lambda handler: _FakeOpener(),
    )
    conn = _FakeBBConn()
    result = put_file(
        conn,
        repo_full_name="acme-workspace/private-svc",
        path=".gitlab-ci.yml",
        branch="main",
        content="# ci\n",
        commit_message="add CI",
    )
    assert captured["method"] == "POST"
    assert "multipart/form-data" in captured["content_type"]
    assert b".gitlab-ci.yml" in captured["data"]
    assert b"# ci\n" in captured["data"]
    assert result.file_path == ".gitlab-ci.yml"
    assert result.commit_sha == "abc123sha"


# ---------------------------------------------------------------------------
# install_bitbucket_webhook
# ---------------------------------------------------------------------------


def test_install_webhook_success(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=15: _json_resp(
            {"uuid": "{abc-123}", "url": "https://astrolift.example.com/webhooks/scm"}
        ),
    )
    conn = _FakeBBConn()
    result = install_webhook(
        conn,
        repo_full_name="acme-workspace/private-svc",
        target_url="https://astrolift.example.com/webhooks/scm",
        secret="s3cr3t",
    )
    assert result.hook_id == "{abc-123}"
    assert result.webhook_url == "https://astrolift.example.com/webhooks/scm"


def test_install_webhook_auth_failure_recoverable(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(_http_error(401)),
    )
    conn = _FakeBBConn()
    with pytest.raises(ProviderError) as exc_info:
        install_webhook(
            conn,
            repo_full_name="acme-workspace/private-svc",
            target_url="https://astrolift.example.com/webhooks/scm",
            secret="s3cr3t",
        )
    assert exc_info.value.code == "AUTH_FAILED"
    assert exc_info.value.recoverable is True


# ---------------------------------------------------------------------------
# open_bitbucket_pull_request
# ---------------------------------------------------------------------------


def test_open_pull_request_success(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=15: _json_resp(
            {
                "id": 42,
                "links": {
                    "html": {"href": "https://bitbucket.org/acme-workspace/private-svc/pull-requests/42"}
                },
            }
        ),
    )
    conn = _FakeBBConn()
    result = open_pull_request(
        conn,
        repo_full_name="acme-workspace/private-svc",
        head_branch="feature/x",
        base_branch="main",
        title="Add feature X",
        body="Details.",
    )
    assert result.url == "https://bitbucket.org/acme-workspace/private-svc/pull-requests/42"
    assert result.number == "42"
    assert result.head_branch == "feature/x"
    assert result.base_branch == "main"


def test_open_pull_request_body_uses_source_destination(monkeypatch):
    """Verify the Bitbucket-specific source/destination shape is sent."""
    captured: dict = {}

    def _fake_urlopen(req, timeout=15):
        captured["body"] = json.loads(req.data)
        return _json_resp({"id": 1, "links": {"html": {"href": "https://bitbucket.org/x/y/pull-requests/1"}}})

    monkeypatch.setattr("astrolift_scm.providers.bitbucket.urllib.request.urlopen", _fake_urlopen)
    conn = _FakeBBConn()
    open_pull_request(
        conn,
        repo_full_name="acme-workspace/private-svc",
        head_branch="feat",
        base_branch="main",
        title="T",
        body="",
    )
    assert captured["body"]["source"]["branch"]["name"] == "feat"
    assert captured["body"]["destination"]["branch"]["name"] == "main"


# ---------------------------------------------------------------------------
# delete_bitbucket_webhook
# ---------------------------------------------------------------------------


def test_delete_webhook_success(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=10: _FakeResp(b"", url="https://api.bitbucket.org/done"),
    )
    conn = _FakeBBConn()
    delete_webhook(conn, repo_full_name="acme-workspace/private-svc", hook_id="{abc-123}")


def test_delete_webhook_404_idempotent(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(404)),
    )
    conn = _FakeBBConn()
    delete_webhook(conn, repo_full_name="acme-workspace/private-svc", hook_id="{abc-123}")


def test_delete_webhook_auth_failure_recoverable(monkeypatch):
    monkeypatch.setattr(
        "astrolift_scm.providers.bitbucket.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(403)),
    )
    conn = _FakeBBConn()
    with pytest.raises(ProviderError) as exc_info:
        delete_webhook(conn, repo_full_name="acme-workspace/private-svc", hook_id="{abc-123}")
    assert exc_info.value.code == "AUTH_FAILED"
    assert exc_info.value.recoverable is True


# ---------------------------------------------------------------------------
# Dispatcher routing — oauth_app raises UNSUPPORTED_AUTH
# ---------------------------------------------------------------------------


def test_oauth_app_config_row_returns_recoverable_error(monkeypatch):
    conn = _FakeBBConn(kind="bitbucket_oauth_app")
    with pytest.raises(ProviderError) as exc_info:
        list(list_repos(conn))
    assert exc_info.value.code == "UNSUPPORTED_AUTH"
    assert exc_info.value.recoverable is True
