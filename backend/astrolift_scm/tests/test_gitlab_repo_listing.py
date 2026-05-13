"""
GitLab repo-listing path: visibility-scope filter + auth error mapping.

Mirrors ``test_repo_listing.py`` for the GitHub driver. The HTTP
call is patched; we drive the dispatcher with realistic GitLab
``/api/v4/projects`` payloads so the scope filter + error mapping
get covered without touching the network.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_scm.providers import ProviderError, fetch_file, list_repos


class _FakeConn:
    """Minimal SourceConnection-shaped stand-in for the GitLab driver."""

    def __init__(
        self,
        *,
        kind="gitlab_pat",
        account_login="acme-group",
        scopes=None,
        api_base_url="",
    ):
        self.kind = kind
        self.account_login = account_login
        self.repo_visibility_scopes = scopes or []
        self.api_base_url = api_base_url
        self.secret_backend_kind = "local_fernet"
        from core.secrets import encrypt_at_rest

        self.secret_ciphertext = encrypt_at_rest(b"glpat-TestTokenNeverHitsTheNetwork").backend_ref


class _MockResponse:
    def __init__(self, body):
        if isinstance(body, (dict, list)):
            self._body = json.dumps(body).encode("utf-8")
        else:
            self._body = body.encode("utf-8") if isinstance(body, str) else body

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body)

    def __exit__(self, *_):
        return False


_PROJECTS = [
    {
        "path_with_namespace": "acme-group/private-svc",
        "name": "private-svc",
        "description": "internal service",
        "default_branch": "main",
        "visibility": "private",
        "namespace": {"full_path": "acme-group", "kind": "group"},
        "http_url_to_repo": "https://gitlab.com/acme-group/private-svc.git",
        "ssh_url_to_repo": "git@gitlab.com:acme-group/private-svc.git",
        "web_url": "https://gitlab.com/acme-group/private-svc",
        "archived": False,
        "forked_from_project": None,
        "last_activity_at": "2026-05-01T12:00:00Z",
    },
    {
        "path_with_namespace": "acme-group/public-cli",
        "name": "public-cli",
        "description": "open-source cli",
        "default_branch": "main",
        "visibility": "public",
        "namespace": {"full_path": "acme-group", "kind": "group"},
        "http_url_to_repo": "https://gitlab.com/acme-group/public-cli.git",
        "ssh_url_to_repo": "git@gitlab.com:acme-group/public-cli.git",
        "web_url": "https://gitlab.com/acme-group/public-cli",
        "archived": False,
        "forked_from_project": None,
        "last_activity_at": "2026-04-12T08:00:00Z",
    },
    {
        "path_with_namespace": "alice/personal-blog",
        "name": "personal-blog",
        "description": "blog",
        "default_branch": "main",
        "visibility": "public",
        "namespace": {"full_path": "alice", "kind": "user"},
        "http_url_to_repo": "https://gitlab.com/alice/personal-blog.git",
        "ssh_url_to_repo": "git@gitlab.com:alice/personal-blog.git",
        "web_url": "https://gitlab.com/alice/personal-blog",
        "archived": False,
        "forked_from_project": None,
        "last_activity_at": "2026-03-30T09:00:00Z",
    },
    {
        "path_with_namespace": "torvalds/linux-fork",
        "name": "linux-fork",
        "description": "kernel fork",
        "default_branch": "master",
        "visibility": "public",
        "namespace": {"full_path": "torvalds", "kind": "user"},
        "http_url_to_repo": "https://gitlab.com/torvalds/linux-fork.git",
        "ssh_url_to_repo": "git@gitlab.com:torvalds/linux-fork.git",
        "web_url": "https://gitlab.com/torvalds/linux-fork",
        "archived": False,
        "forked_from_project": {"id": 9999},
        "last_activity_at": "2026-05-07T22:11:00Z",
    },
]


@pytest.fixture
def patched_urlopen():
    with patch("astrolift_scm.providers.gitlab.urllib.request.urlopen") as m:
        m.return_value = _MockResponse(_PROJECTS)
        yield m


def test_no_scopes_means_no_filter(patched_urlopen):
    conn = _FakeConn(scopes=[])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {
        "acme-group/private-svc",
        "acme-group/public-cli",
        "alice/personal-blog",
        "torvalds/linux-fork",
    }


def test_private_org_scope_filters_to_private_org_projects(patched_urlopen):
    conn = _FakeConn(scopes=["private_org"])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {"acme-group/private-svc"}


def test_public_org_scope(patched_urlopen):
    conn = _FakeConn(scopes=["public_org"])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {"acme-group/public-cli"}


def test_user_repos_scope(patched_urlopen):
    """GitLab user-namespace projects count as user repos."""
    conn = _FakeConn(scopes=["user_repos"])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {
        "alice/personal-blog",
        "torvalds/linux-fork",
    }


def test_fork_flag_is_propagated(patched_urlopen):
    conn = _FakeConn(scopes=[])
    rows = {r.full_name: r for r in list_repos(conn)}
    assert rows["torvalds/linux-fork"].is_fork is True
    assert rows["acme-group/private-svc"].is_fork is False


def test_visibility_normalizes_private_vs_public(patched_urlopen):
    """GitLab has private/internal/public; the normalized RemoteRepo
    only carries private vs public. Internal must surface as private
    (it isn't world-readable)."""
    payload = [
        {
            "path_with_namespace": "acme-group/internal-svc",
            "name": "internal-svc",
            "description": "",
            "default_branch": "main",
            "visibility": "internal",
            "namespace": {"full_path": "acme-group", "kind": "group"},
            "http_url_to_repo": "",
            "ssh_url_to_repo": "",
            "web_url": "",
            "archived": False,
            "forked_from_project": None,
            "last_activity_at": "2026-05-01T12:00:00Z",
        }
    ]
    patched_urlopen.return_value = _MockResponse(payload)
    conn = _FakeConn(scopes=[])
    rows = list(list_repos(conn))
    assert rows[0].visibility == "private"


def test_oauth_app_config_row_returns_recoverable_error(patched_urlopen):
    """Listing repos against a gitlab_oauth_app config row (no token
    yet) must fail-recoverable so the UI renders 'complete the
    OAuth dance' affordance."""
    conn = _FakeConn(kind="gitlab_oauth_app")
    with pytest.raises(ProviderError) as exc:
        list_repos(conn)
    assert exc.value.code == "OAUTH_NOT_CONNECTED"
    assert exc.value.recoverable is True


def test_auth_failure_is_recoverable(monkeypatch):
    """A 401 from GitLab must come back as recoverable so the UI
    surfaces 'reconnect' instead of a 500."""
    import urllib.error

    def _boom(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad token"))

    monkeypatch.setattr("astrolift_scm.providers.gitlab.urllib.request.urlopen", _boom)
    conn = _FakeConn()
    with pytest.raises(ProviderError) as exc:
        list_repos(conn)
    assert exc.value.code == "AUTH_FAILED"
    assert exc.value.recoverable is True


def test_self_hosted_base_url_is_used(monkeypatch):
    """When ``api_base_url`` is set, the driver must hit that host —
    not gitlab.com. Captured by intercepting the Request URL."""
    captured = {}

    def _capture(req, timeout=10):
        captured["url"] = req.full_url
        return _MockResponse([])

    monkeypatch.setattr("astrolift_scm.providers.gitlab.urllib.request.urlopen", _capture)
    conn = _FakeConn(api_base_url="https://gitlab.acme.example/")
    list(list_repos(conn))
    assert captured["url"].startswith("https://gitlab.acme.example/api/v4/projects")


def test_fetch_file_returns_content(monkeypatch):
    """``fetch_file`` returns the file body on 200."""
    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        lambda req, timeout=10: _MockResponse('name = "demo"\n'),
    )
    conn = _FakeConn()
    body = fetch_file(
        conn,
        repo_full_name="acme-group/private-svc",
        path="astrolift.toml",
        ref="main",
    )
    assert body == 'name = "demo"\n'


def test_fetch_file_returns_none_on_404(monkeypatch):
    import urllib.error

    def _boom(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b"missing"))

    monkeypatch.setattr("astrolift_scm.providers.gitlab.urllib.request.urlopen", _boom)
    conn = _FakeConn()
    assert (
        fetch_file(
            conn,
            repo_full_name="acme-group/private-svc",
            path="missing.toml",
            ref="main",
        )
        is None
    )
