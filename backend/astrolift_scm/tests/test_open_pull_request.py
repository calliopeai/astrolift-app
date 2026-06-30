"""Tests for the ``open_pull_request`` provider dispatch + the
GitHub / GitLab driver implementations (#535).

HTTP traffic is stubbed at ``urllib.request.urlopen`` — same pattern
used by ``test_push_ci_workflow.py``. The tests exercise the path
matrix: happy-path PR creation on GitHub + MR creation on GitLab,
plus the auth-failure / already-exists / unsupported-host edges that
the mutation layer needs the recoverable-error envelope for.
"""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import (
    ProviderError,
    PullRequestResult,
    open_pull_request,
)
from core.secrets import encrypt_at_rest

pytestmark = pytest.mark.django_db


def _make_github_conn(*, account_login: str = "alice") -> SourceConnection:
    encrypted = encrypt_at_rest(b"gho_test_user_token_never_hits_the_network")
    org = Organization.objects.create(name=f"Acme-{account_login}", slug=f"acme-{account_login}")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name=f"GitHub: {account_login}",
        account_login=account_login,
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _make_gitlab_conn(*, account_login: str = "alice") -> SourceConnection:
    encrypted = encrypt_at_rest(b"glat-test-user-token-never-hits-the-network")
    org = Organization.objects.create(
        name=f"Acme-gl-{account_login}",
        slug=f"acme-gl-{account_login}",
    )
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
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body, status=self.status)

    def __exit__(self, *_):
        return False


def _http_response(payload=None, *, status: int = 200) -> _CMResponse:
    return _CMResponse(json.dumps(payload or {}).encode("utf-8"), status=status)


def _http_error(url: str, code: int, reason: str = "", body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, reason, {}, io.BytesIO(body))


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def test_github_open_pull_request_happy_path(monkeypatch):
    conn = _make_github_conn(account_login="github-happy")
    captured_url = []
    captured_body = {}

    def fake_urlopen(req, timeout=15):
        captured_url.append(req.full_url)
        captured_body.update(json.loads(req.data.decode("utf-8")))
        return _http_response(
            {
                "html_url": "https://github.com/acme/api/pull/77",
                "number": 77,
            }
        )

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    result = open_pull_request(
        conn,
        repo_full_name="acme/api",
        head_branch="feature-x",
        base_branch="main",
        title="my pr",
        body="opens a PR",
    )

    assert isinstance(result, PullRequestResult)
    assert result.url == "https://github.com/acme/api/pull/77"
    assert result.number == "77"
    assert result.head_branch == "feature-x"
    assert result.base_branch == "main"
    assert captured_url[0].endswith("/repos/acme/api/pulls")
    assert captured_body["head"] == "feature-x"
    assert captured_body["base"] == "main"
    assert captured_body["title"] == "my pr"
    assert captured_body["body"] == "opens a PR"
    assert captured_body["maintainer_can_modify"] is True


def test_github_open_pull_request_auth_failure_is_recoverable(monkeypatch):
    conn = _make_github_conn(account_login="gh-auth-fail")

    def fake_urlopen(req, timeout=15):
        raise _http_error(req.full_url, 401, "Unauthorized", b"bad token")

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(ProviderError) as exc:
        open_pull_request(
            conn,
            repo_full_name="acme/api",
            head_branch="feature-x",
            base_branch="main",
            title="t",
            body="b",
        )
    assert exc.value.code == "AUTH_FAILED"
    assert exc.value.recoverable is True


def test_github_open_pull_request_already_exists(monkeypatch):
    """422 with 'a pull request already exists' maps to a recoverable
    ALREADY_EXISTS so the mutation can show 'PR already open' rather
    than the raw API error."""
    conn = _make_github_conn(account_login="gh-dupe")

    def fake_urlopen(req, timeout=15):
        raise _http_error(
            req.full_url,
            422,
            "Unprocessable Entity",
            b'{"errors":[{"message":"A pull request already exists for acme:feature-x."}]}',
        )

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(ProviderError) as exc:
        open_pull_request(
            conn,
            repo_full_name="acme/api",
            head_branch="feature-x",
            base_branch="main",
            title="t",
            body="b",
        )
    assert exc.value.code == "ALREADY_EXISTS"
    assert exc.value.recoverable is True


def test_github_open_pull_request_no_commits_between(monkeypatch):
    """422 with 'No commits between' surfaces as NOTHING_TO_MERGE so
    the mutation can return a user-meaningful no-op rather than the
    raw API error."""
    conn = _make_github_conn(account_login="gh-empty")

    def fake_urlopen(req, timeout=15):
        raise _http_error(
            req.full_url,
            422,
            "Unprocessable Entity",
            b'{"errors":[{"message":"No commits between main and feature-x"}]}',
        )

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(ProviderError) as exc:
        open_pull_request(
            conn,
            repo_full_name="acme/api",
            head_branch="feature-x",
            base_branch="main",
            title="t",
            body="b",
        )
    assert exc.value.code == "NOTHING_TO_MERGE"


def test_github_open_pull_request_unexpected_shape(monkeypatch):
    """Response is missing html_url / number — driver refuses to
    fabricate a return value."""
    conn = _make_github_conn(account_login="gh-shape")

    def fake_urlopen(req, timeout=15):
        return _http_response({"unexpected": True})

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(ProviderError) as exc:
        open_pull_request(
            conn,
            repo_full_name="acme/api",
            head_branch="feature-x",
            base_branch="main",
            title="t",
            body="b",
        )
    assert exc.value.code == "UNEXPECTED_SHAPE"


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


def test_gitlab_open_merge_request_happy_path(monkeypatch):
    conn = _make_gitlab_conn(account_login="gl-happy")
    captured_url = []
    captured_body = {}

    def fake_urlopen(req, timeout=15):
        captured_url.append(req.full_url)
        captured_body.update(json.loads(req.data.decode("utf-8")))
        return _http_response(
            {
                "web_url": "https://gitlab.com/acme/api/-/merge_requests/9",
                "iid": 9,
            }
        )

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        fake_urlopen,
    )

    result = open_pull_request(
        conn,
        repo_full_name="acme/api",
        head_branch="feature-x",
        base_branch="main",
        title="my mr",
        body="opens an MR",
    )

    assert result.url == "https://gitlab.com/acme/api/-/merge_requests/9"
    assert result.number == "9"
    assert result.head_branch == "feature-x"
    assert result.base_branch == "main"
    # GitLab uses URL-encoded ``path_with_namespace`` for the project id.
    assert captured_url[0].endswith("/api/v4/projects/acme%2Fapi/merge_requests")
    assert captured_body["source_branch"] == "feature-x"
    assert captured_body["target_branch"] == "main"
    assert captured_body["title"] == "my mr"


def test_gitlab_open_merge_request_already_exists(monkeypatch):
    """409 or 4xx with 'already exists' → recoverable ALREADY_EXISTS."""
    conn = _make_gitlab_conn(account_login="gl-dupe")

    def fake_urlopen(req, timeout=15):
        raise _http_error(
            req.full_url,
            409,
            "Conflict",
            b'{"message":["Another open merge request already exists for this source branch"]}',
        )

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(ProviderError) as exc:
        open_pull_request(
            conn,
            repo_full_name="acme/api",
            head_branch="feature-x",
            base_branch="main",
            title="t",
            body="b",
        )
    assert exc.value.code == "ALREADY_EXISTS"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def test_open_pull_request_unsupported_kind_raises(monkeypatch):
    """A kind the dispatcher doesn't know about maps to a UNSUPPORTED
    ProviderError rather than crashing — the mutation layer translates
    this into a user-friendly envelope."""
    encrypted = encrypt_at_rest(b"unused")
    org = Organization.objects.create(name="Bb", slug="bb")
    # GitHub/GitLab/Bitbucket/Gitea are all supported now, so the only
    # way to reach the unsupported-host path is a kind string the
    # dispatcher's host sets don't cover. ``kind`` is a CharField whose
    # choices aren't DB-enforced, so a synthetic value exercises the
    # final ``UNSUPPORTED`` fallthrough directly.
    conn = SourceConnection.objects.create(
        organization=org,
        kind="mercurial_pat",
        display_name="Mercurial",
        account_login="alice",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )

    with pytest.raises(ProviderError) as exc:
        open_pull_request(
            conn,
            repo_full_name="acme/api",
            head_branch="feature-x",
            base_branch="main",
            title="t",
            body="b",
        )
    assert exc.value.code == "UNSUPPORTED"
