"""Commit resolution uses stored host credentials and fails without an immutable SHA."""

import base64
import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_scm.providers import ProviderError, revisions
from core.secrets import encrypt_at_rest

SHA = "ab" * 20


def _connection(kind="github_pat", base=""):
    secret = encrypt_at_rest(b"test-revision-token")
    return SimpleNamespace(
        kind=kind,
        api_base_url=base,
        account_login="acme",
        secret_backend_kind=secret.backend_kind,
        secret_ciphertext=secret.backend_ref,
    )


@pytest.mark.parametrize(
    "kind,base,path,field,auth",
    [
        (
            "github_pat",
            "https://github.example/api/v3",
            "/repos/acme/web/commits/",
            "sha",
            "token test-revision-token",
        ),
        ("github_oauth_user", "", "/repos/acme/web/commits/", "sha", "token test-revision-token"),
        ("github_app_install", "", "/repos/acme/web/commits/", "sha", "Bearer installation-test-token"),
        (
            "gitlab_pat",
            "https://gitlab.example",
            "/api/v4/projects/acme%2Fweb/repository/commits/",
            "id",
            "Bearer test-revision-token",
        ),
        (
            "gitea_pat",
            "https://gitea.example",
            "/api/v1/repos/acme/web/git/commits/",
            "sha",
            "token test-revision-token",
        ),
        (
            "bitbucket_pat",
            "",
            "/2.0/repositories/acme/web/commit/",
            "hash",
            "Basic " + base64.b64encode(b"acme:test-revision-token").decode(),
        ),
        (
            "bitbucket_oauth_user",
            "",
            "/2.0/repositories/acme/web/commit/",
            "hash",
            "Bearer test-revision-token",
        ),
    ],
)
def test_resolves_escaped_ref_with_host_credentials(monkeypatch, kind, base, path, field, auth):
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token", lambda conn: "installation-test-token"
    )
    seen = []

    def request(req, *, timeout):
        seen.append(req)
        assert timeout == 10
        assert req.get_header("Authorization") == auth
        return io.BytesIO(json.dumps({field: SHA.upper()}).encode())

    monkeypatch.setattr(revisions.urllib.request, "urlopen", request)
    assert (
        revisions.fetch_commit(_connection(kind, base), repo_full_name="acme/web", ref="release/v2#1") == SHA
    )
    expected_base = base or (
        "https://api.bitbucket.org" if kind.startswith("bitbucket") else "https://api.github.com"
    )
    assert seen[0].full_url == expected_base + path + "release%2Fv2%231"


@pytest.mark.parametrize(
    "status,headers,code,recoverable",
    [
        (401, {}, "AUTH_FAILED", True),
        (403, {}, "AUTH_FAILED", True),
        (403, {"X-RateLimit-Remaining": "0"}, "RATE_LIMITED", True),
        (429, {}, "RATE_LIMITED", True),
        (404, {}, "NOT_FOUND", False),
        (500, {}, "API_ERROR", False),
    ],
)
def test_http_errors_fail_without_a_fallback(monkeypatch, status, headers, code, recoverable):
    def request(req, *, timeout):
        raise urllib.error.HTTPError(
            req.full_url, status, "host error", headers, io.BytesIO(b"sensitive provider detail")
        )

    monkeypatch.setattr(revisions.urllib.request, "urlopen", request)
    with pytest.raises(ProviderError) as caught:
        revisions.fetch_commit(_connection(), repo_full_name="acme/web", ref="missing")
    assert (caught.value.code, caught.value.recoverable) == (code, recoverable)
    assert "sensitive" not in str(caught.value)


@pytest.mark.parametrize(
    "payload", [b"not json", b"[]", b"{}", b'{"sha":"main"}', b'{"sha":"abc123"}', b'{"sha":null}']
)
def test_invalid_commit_response_is_rejected(monkeypatch, payload):
    monkeypatch.setattr(revisions.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(payload))
    with pytest.raises(ProviderError, match="commit") as caught:
        revisions.fetch_commit(_connection(), repo_full_name="acme/web", ref="main")
    assert caught.value.code == "UNEXPECTED_SHAPE"


@pytest.mark.parametrize("error", [urllib.error.URLError("offline"), TimeoutError()])
def test_network_errors_are_recoverable(monkeypatch, error):
    def request(*args, **kwargs):
        raise error

    monkeypatch.setattr(revisions.urllib.request, "urlopen", request)
    with pytest.raises(ProviderError) as caught:
        revisions.fetch_commit(_connection(), repo_full_name="acme/web", ref="main")
    assert caught.value.code == "NETWORK"
    assert caught.value.recoverable


def test_unreadable_stored_credential_is_a_provider_error():
    connection = _connection()
    connection.secret_ciphertext = b"not-valid-ciphertext"
    with pytest.raises(ProviderError) as caught:
        revisions.fetch_commit(connection, repo_full_name="acme/web", ref="main")
    assert caught.value.code == "CREDENTIAL_UNAVAILABLE"
    assert caught.value.recoverable
