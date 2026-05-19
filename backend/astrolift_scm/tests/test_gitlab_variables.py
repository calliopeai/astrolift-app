"""Tests for ``put_gitlab_project_variable`` (#531).

Covers the create / update fallback (POST first, PUT on already-exists)
and the auth / network error mapping. Network is patched at
``urllib.request.urlopen`` — no live calls.
"""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers.gitlab import (
    GitlabProviderError,
    put_gitlab_project_variable,
)
from core.secrets import encrypt_at_rest

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Vars", slug="vars-org")


@pytest.fixture
def connection(org):
    encrypted = encrypt_at_rest(b"glpat_xxx")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITLAB_PAT,
        account_login="vars-acct",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


class _Resp:
    def __init__(self, status: int = 201, body: bytes = b""):
        self._status = status
        self._body = body

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body, status=self._status)

    def __exit__(self, *_):
        return False


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://gitlab.example", code, "err", {}, io.BytesIO(body))


def test_put_variable_creates_on_first_call(monkeypatch, connection):
    """First POST returns 201 — no fallback path runs."""
    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append((req.full_url, req.get_method(), json.loads(req.data.decode("utf-8"))))
        return _Resp(status=201)

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        fake_urlopen,
    )

    put_gitlab_project_variable(
        connection,
        repo_full_name="acme/api",
        key="ASTROLIFT_X",
        value="abc12345",  # long enough + no special chars → mask-eligible
    )

    assert len(calls) == 1
    url, method, body = calls[0]
    assert method == "POST"
    assert url.endswith("/api/v4/projects/acme%2Fapi/variables")
    assert body["key"] == "ASTROLIFT_X"
    assert body["value"] == "abc12345"
    assert body["masked"] is True
    assert body["protected"] is False


def test_put_variable_falls_through_to_put_when_exists(monkeypatch, connection):
    """POST returns 400 with ``has already been taken`` → driver retries
    via PUT against the keyed endpoint."""
    state = {"calls": []}

    def fake_urlopen(req, timeout=15):
        method = req.get_method()
        state["calls"].append((req.full_url, method))
        if method == "POST":
            raise _http_error(
                400,
                b'{"message":{"key":["has already been taken"]}}',
            )
        return _Resp(status=200)

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        fake_urlopen,
    )

    put_gitlab_project_variable(
        connection,
        repo_full_name="acme/api",
        key="ASTROLIFT_X",
        value="abc12345",
    )

    methods = [m for _, m in state["calls"]]
    assert methods == ["POST", "PUT"]
    put_url = state["calls"][1][0]
    assert put_url.endswith("/variables/ASTROLIFT_X")


def test_put_variable_surfaces_auth_failure(monkeypatch, connection):
    def fake_urlopen(req, timeout=15):
        raise _http_error(401)

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(GitlabProviderError) as excinfo:
        put_gitlab_project_variable(
            connection,
            repo_full_name="acme/api",
            key="X",
            value="abc12345",
        )
    assert excinfo.value.code == "AUTH_FAILED"
    assert excinfo.value.recoverable is True


def test_put_variable_surfaces_not_found_project(monkeypatch, connection):
    def fake_urlopen(req, timeout=15):
        raise _http_error(404)

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(GitlabProviderError) as excinfo:
        put_gitlab_project_variable(
            connection,
            repo_full_name="acme/api",
            key="X",
            value="abc12345",
        )
    assert excinfo.value.code == "NOT_FOUND"


def test_put_variable_surfaces_api_error_on_unrelated_400(monkeypatch, connection):
    """A 400 that isn't ``already taken`` (e.g. mask-eligibility check
    failed) bubbles as API_ERROR rather than silently retrying as PUT."""

    def fake_urlopen(req, timeout=15):
        raise _http_error(
            400,
            b'{"message":["Value contains characters not allowed for masked variables"]}',
        )

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(GitlabProviderError) as excinfo:
        put_gitlab_project_variable(
            connection,
            repo_full_name="acme/api",
            key="X",
            value="abc12345",
        )
    assert excinfo.value.code == "API_ERROR"
