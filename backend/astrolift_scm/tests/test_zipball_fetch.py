"""Zipball-fetch capability on the SCM provider dispatcher.

``fetch_zipball`` lets a caller (the agent brief assembler) pull a repo's
source archive through any supported host via a stored connection rather
than hardcoding GitHub. The HTTP boundary is patched; we assert the
dispatcher routes by ``connection.kind``, builds the right archive URL,
returns the raw bytes, and refuses unsupported hosts with a clean
ProviderError.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_scm.providers import ProviderError, fetch_branch_head, fetch_zipball


class _FakeConn:
    """Minimal SourceConnection-shaped stand-in for the drivers."""

    def __init__(self, *, kind, api_base_url=""):
        self.kind = kind
        self.api_base_url = api_base_url
        self.account_login = ""
        self.repo_visibility_scopes = []
        self.secret_backend_kind = "local_fernet"
        from core.secrets import encrypt_at_rest

        self.secret_ciphertext = encrypt_at_rest(b"tok-NeverHitsTheNetwork").backend_ref


class _MockResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return SimpleNamespace(read=lambda *_args: self._body)

    def __exit__(self, *_):
        return False


_ZIP_BYTES = b"PK\x03\x04fake-zip-bytes"


def test_github_zipball_routes_and_returns_bytes():
    conn = _FakeConn(kind="github_pat")
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return _MockResponse(_ZIP_BYTES)

    with patch("urllib.request.urlopen", _fake_urlopen):
        out = fetch_zipball(conn, repo_full_name="owner/repo", ref="main")

    assert out == _ZIP_BYTES
    assert captured["url"].endswith("/repos/owner/repo/zipball/main")
    assert captured["auth"].startswith("token ")


def test_github_branch_head_resolves_current_immutable_sha():
    conn = _FakeConn(kind="github_pat")
    captured = {}
    sha = "d" * 40

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return _MockResponse((f'{{"commit":{{"sha":"{sha}"}}}}').encode())

    with patch("urllib.request.urlopen", _fake_urlopen):
        out = fetch_branch_head(conn, repo_full_name="owner/repo", branch="feature/x")

    assert out == sha
    assert captured["url"].endswith("/repos/owner/repo/branches/feature%2Fx")
    assert captured["auth"].startswith("token ")


def test_gitlab_zipball_routes_to_archive_endpoint():
    conn = _FakeConn(kind="gitlab_pat")
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return _MockResponse(_ZIP_BYTES)

    with patch("urllib.request.urlopen", _fake_urlopen):
        out = fetch_zipball(conn, repo_full_name="acme-group/svc", ref="release")

    assert out == _ZIP_BYTES
    # GitLab addresses the project by URL-encoded path_with_namespace.
    assert "/api/v4/projects/acme-group%2Fsvc/repository/archive.zip" in captured["url"]
    assert captured["url"].endswith("?sha=release")
    assert captured["auth"].startswith("Bearer ")


def test_gitlab_self_hosted_base_url_is_honored():
    conn = _FakeConn(kind="gitlab_pat", api_base_url="https://gitlab.acme.example/")
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _MockResponse(_ZIP_BYTES)

    with patch("urllib.request.urlopen", _fake_urlopen):
        fetch_zipball(conn, repo_full_name="g/p", ref="main")

    assert captured["url"].startswith("https://gitlab.acme.example/api/v4/projects/")


def test_unsupported_host_raises_provider_error():
    conn = _FakeConn(kind="bitbucket_pat")
    with pytest.raises(ProviderError) as exc:
        fetch_zipball(conn, repo_full_name="owner/repo", ref="main")
    assert exc.value.code == "UNSUPPORTED"


def test_gitlab_auth_failure_maps_to_recoverable_provider_error():
    import urllib.error

    conn = _FakeConn(kind="gitlab_pat")

    def _fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    with patch("urllib.request.urlopen", _fake_urlopen):
        with pytest.raises(ProviderError) as exc:
            fetch_zipball(conn, repo_full_name="g/p", ref="main")

    assert exc.value.code == "AUTH_FAILED"
    assert exc.value.recoverable is True
