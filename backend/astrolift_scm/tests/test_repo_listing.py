"""
Repo-listing path: visibility-scope filter + auth error mapping.

The HTTP call to GitHub is patched out — we test the filter logic
and the error translation, not the network. End-to-end with a real
PAT is a manual smoke (paste a token, hit /apps/new picker).
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_scm.providers import ProviderError, list_repos


class _FakeConn:
    """Minimal SourceConnection-shaped stand-in. Avoids spinning the
    org/migration overhead just to test the filter; the encryption
    layer is tested separately in test_secrets_and_keygen."""

    def __init__(
        self,
        *,
        kind="github_pat",
        account_login="acme-org",
        scopes=None,
        api_base_url="",
    ):
        self.kind = kind
        self.account_login = account_login
        self.repo_visibility_scopes = scopes or []
        self.api_base_url = api_base_url
        self.secret_backend_kind = "local_fernet"
        # Encrypt a known PAT so the driver's decrypt path runs end-to-end.
        from core.secrets import encrypt_at_rest

        self.secret_ciphertext = encrypt_at_rest(b"ghp_TestTokenNeverHitsTheNetwork").backend_ref


class _MockResponse:
    """urllib-style context manager that hands back a fixed body."""

    def __init__(self, rows):
        self._body = json.dumps(rows).encode("utf-8")

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body)

    def __exit__(self, *_):
        return False


def _mock_response(rows):
    return _MockResponse(rows)


_REPOS = [
    {
        "full_name": "acme-org/private-svc",
        "name": "private-svc",
        "description": "internal service",
        "default_branch": "main",
        "private": True,
        "owner": {"login": "acme-org", "type": "Organization"},
        "clone_url": "https://github.com/acme-org/private-svc.git",
        "ssh_url": "git@github.com:acme-org/private-svc.git",
        "html_url": "https://github.com/acme-org/private-svc",
        "archived": False,
        "fork": False,
        "pushed_at": "2026-05-01T12:00:00Z",
    },
    {
        "full_name": "acme-org/public-cli",
        "name": "public-cli",
        "description": "open-source cli",
        "default_branch": "main",
        "private": False,
        "owner": {"login": "acme-org", "type": "Organization"},
        "clone_url": "https://github.com/acme-org/public-cli.git",
        "ssh_url": "git@github.com:acme-org/public-cli.git",
        "html_url": "https://github.com/acme-org/public-cli",
        "archived": False,
        "fork": False,
        "pushed_at": "2026-04-12T08:00:00Z",
    },
    {
        "full_name": "alice/personal-blog",
        "name": "personal-blog",
        "description": "blog",
        "default_branch": "main",
        "private": False,
        "owner": {"login": "alice", "type": "User"},
        "clone_url": "https://github.com/alice/personal-blog.git",
        "ssh_url": "git@github.com:alice/personal-blog.git",
        "html_url": "https://github.com/alice/personal-blog",
        "archived": False,
        "fork": False,
        "pushed_at": "2026-03-30T09:00:00Z",
    },
    {
        "full_name": "torvalds/linux",
        "name": "linux",
        "description": "kernel",
        "default_branch": "master",
        "private": False,
        "owner": {"login": "torvalds", "type": "User"},
        "clone_url": "https://github.com/torvalds/linux.git",
        "ssh_url": "git@github.com:torvalds/linux.git",
        "html_url": "https://github.com/torvalds/linux",
        "archived": False,
        "fork": True,
        "pushed_at": "2026-05-07T22:11:00Z",
    },
]


@pytest.fixture
def patched_urlopen():
    with patch("astrolift_scm.providers.github.urllib.request.urlopen") as m:
        m.return_value = _mock_response(_REPOS)
        yield m


def test_no_scopes_means_no_filter(patched_urlopen):
    conn = _FakeConn(scopes=[])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {
        "acme-org/private-svc",
        "acme-org/public-cli",
        "alice/personal-blog",
        "torvalds/linux",
    }


def test_private_org_scope_filters_to_private_org_repos(patched_urlopen):
    conn = _FakeConn(scopes=["private_org"])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {"acme-org/private-svc"}


def test_public_org_scope(patched_urlopen):
    conn = _FakeConn(scopes=["public_org"])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {"acme-org/public-cli"}


def test_user_repos_scope(patched_urlopen):
    conn = _FakeConn(scopes=["user_repos"])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {
        "alice/personal-blog",
        "torvalds/linux",
    }


def test_combined_scopes_union(patched_urlopen):
    conn = _FakeConn(scopes=["private_org", "public_org"])
    rows = list(list_repos(conn))
    assert {r.full_name for r in rows} == {
        "acme-org/private-svc",
        "acme-org/public-cli",
    }


def test_search_substring_filter(patched_urlopen):
    conn = _FakeConn(scopes=[])
    rows = list(list_repos(conn, search="cli"))
    assert {r.full_name for r in rows} == {"acme-org/public-cli"}


def test_oauth_app_config_row_returns_recoverable_error(patched_urlopen):
    """Trying to list repos against an OAuth-app config row (no token
    yet — just client_id + secret) should fail-recoverable so the UI
    can render 'complete the OAuth dance' affordance."""
    conn = _FakeConn(kind="github_oauth_app")
    with pytest.raises(ProviderError) as exc:
        list_repos(conn)
    assert exc.value.code == "OAUTH_NOT_CONNECTED"
    assert exc.value.recoverable is True


def test_auth_failure_is_recoverable(monkeypatch):
    """A 401 from GitHub must come back as recoverable so the UI
    surfaces 'reconnect' instead of a 500."""
    import urllib.error

    def _boom(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad token"))

    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", _boom)
    conn = _FakeConn()
    with pytest.raises(ProviderError) as exc:
        list_repos(conn)
    assert exc.value.code == "AUTH_FAILED"
    assert exc.value.recoverable is True
