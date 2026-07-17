"""Low-level GitHub Deployments API client — best-effort semantics (#1124).

These cover the ``astrolift_scm.github_deployments`` primitives in
isolation: the real ``urllib`` POST path is exercised (stubbed at
``urlopen``) so the error-swallowing is genuine, and the App-token guard
is verified against real SourceConnection rows. The higher-level lifecycle
wiring lives in ``astrolift_workflows/tests/test_github_deployment_reflection``.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from astrolift_identity.models import Organization
from astrolift_scm import github_deployments
from astrolift_scm.models import SourceConnection

pytestmark = pytest.mark.django_db


_org_counter = 0


def _conn(**overrides):
    global _org_counter
    org = overrides.pop("organization", None)
    if org is None:
        _org_counter += 1
        org = Organization.objects.create(name="ACME", slug=f"acme-ghd-{_org_counter}")
    defaults = {
        "kind": SourceConnection.Kind.GITHUB_APP_INSTALL,
        "account_login": "acme-org",
        "installation_id": "4321",
    }
    defaults.update(overrides)
    return SourceConnection.objects.create(organization=org, **defaults)


class _FakeResp:
    """Minimal urlopen context-manager stand-in."""

    def __init__(self, code: int, body: dict):
        self._code = code
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def getcode(self):
        return self._code

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture_urlopen(monkeypatch, resp=None, raises=None):
    """Patch urlopen to record the Request and return ``resp`` / raise
    ``raises``. Returns a list the caller can inspect."""
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        if raises is not None:
            raise raises
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


def _stub_token(monkeypatch, token="tok-123"):
    monkeypatch.setattr(github_deployments, "_installation_token", lambda connection: token)


# ---- create_github_deployment -----------------------------------------


def test_create_deployment_returns_id_on_success(monkeypatch):
    c = _conn()
    _stub_token(monkeypatch)
    seen = _capture_urlopen(monkeypatch, resp=_FakeResp(201, {"id": 999, "sha": "abc"}))

    dep_id = github_deployments.create_github_deployment(
        c, "acme", "hello", ref="abc123", environment="prod", description="deploy"
    )

    assert dep_id == 999
    assert len(seen) == 1
    req = seen[0]
    assert req.full_url == "https://api.github.com/repos/acme/hello/deployments"
    payload = json.loads(req.data)
    # These two flags are the whole reason the create doesn't 409/auto-merge.
    assert payload["auto_merge"] is False
    assert payload["required_contexts"] == []
    assert payload["ref"] == "abc123"
    assert payload["environment"] == "prod"


def test_create_deployment_none_on_500(monkeypatch):
    """A 500 from GitHub raises HTTPError inside urlopen; the client
    swallows it and returns None — the deploy is never affected."""
    c = _conn()
    _stub_token(monkeypatch)
    err = urllib.error.HTTPError("https://api.github.com/x", 500, "Server Error", {}, io.BytesIO(b"boom"))
    _capture_urlopen(monkeypatch, raises=err)

    dep_id = github_deployments.create_github_deployment(c, "acme", "hello", ref="abc", environment="prod")
    assert dep_id is None


def test_create_deployment_none_on_network_error(monkeypatch):
    c = _conn()
    _stub_token(monkeypatch)
    _capture_urlopen(monkeypatch, raises=urllib.error.URLError("no route to host"))

    dep_id = github_deployments.create_github_deployment(c, "acme", "hello", ref="abc", environment="prod")
    assert dep_id is None


def test_create_deployment_none_on_non_dict_id(monkeypatch):
    """GitHub returned 2xx but no numeric id (unexpected shape) → None."""
    c = _conn()
    _stub_token(monkeypatch)
    _capture_urlopen(monkeypatch, resp=_FakeResp(201, {"message": "queued"}))

    dep_id = github_deployments.create_github_deployment(c, "acme", "hello", ref="abc", environment="prod")
    assert dep_id is None


def test_create_deployment_skips_without_token(monkeypatch):
    """Orphaned connection → no token → no HTTP call, returns None."""
    c = _conn(is_orphaned=True)
    seen = _capture_urlopen(monkeypatch, resp=_FakeResp(201, {"id": 1}))

    dep_id = github_deployments.create_github_deployment(c, "acme", "hello", ref="abc", environment="prod")
    assert dep_id is None
    assert seen == []  # never reached the network


# ---- post_github_deployment_status ------------------------------------


def test_post_status_true_on_2xx_with_links(monkeypatch):
    c = _conn()
    _stub_token(monkeypatch)
    seen = _capture_urlopen(monkeypatch, resp=_FakeResp(201, {"id": 5}))

    ok = github_deployments.post_github_deployment_status(
        c,
        "acme",
        "hello",
        999,
        state="success",
        environment="prod",
        environment_url="https://hello.example.com/",
        log_url="https://astrolift.test/apps/hello/deployments/g1",
    )

    assert ok is True
    req = seen[0]
    assert req.full_url == "https://api.github.com/repos/acme/hello/deployments/999/statuses"
    payload = json.loads(req.data)
    assert payload["state"] == "success"
    assert payload["environment_url"] == "https://hello.example.com/"
    assert payload["log_url"] == "https://astrolift.test/apps/hello/deployments/g1"


def test_post_status_omits_empty_optional_fields(monkeypatch):
    """Empty environment_url must NOT be sent — GitHub renders it as a
    broken 'View deployment' button."""
    c = _conn()
    _stub_token(monkeypatch)
    seen = _capture_urlopen(monkeypatch, resp=_FakeResp(201, {"id": 5}))

    github_deployments.post_github_deployment_status(c, "acme", "hello", 5, state="in_progress")

    payload = json.loads(seen[0].data)
    assert payload == {"state": "in_progress"}


def test_post_status_false_on_http_error(monkeypatch):
    c = _conn()
    _stub_token(monkeypatch)
    err = urllib.error.HTTPError("https://api.github.com/x", 422, "Unprocessable", {}, io.BytesIO(b"bad"))
    _capture_urlopen(monkeypatch, raises=err)

    ok = github_deployments.post_github_deployment_status(c, "acme", "hello", 5, state="failure")
    assert ok is False


def test_post_status_skips_without_token(monkeypatch):
    c = _conn(installation_id="")  # no installation → no token
    seen = _capture_urlopen(monkeypatch, resp=_FakeResp(201, {}))

    ok = github_deployments.post_github_deployment_status(c, "acme", "hello", 5, state="success")
    assert ok is False
    assert seen == []


# ---- token guard ------------------------------------------------------


def test_installation_token_short_circuits_orphaned(monkeypatch):
    """Never even attempt to mint for a dead installation."""
    c = _conn(is_orphaned=True)
    called = {"n": 0}

    def _boom(connection):
        called["n"] += 1
        return "tok"

    monkeypatch.setattr("astrolift_scm.providers.github_app.installation_token", _boom)
    assert github_deployments._installation_token(c) is None
    assert called["n"] == 0


def test_installation_token_swallows_mint_error(monkeypatch):
    """A mint failure (revoked key, unreachable GitHub) is swallowed to
    None — never raised into the deploy path."""
    c = _conn()

    def _raise(connection):
        raise RuntimeError("private key rejected")

    monkeypatch.setattr("astrolift_scm.providers.github_app.installation_token", _raise)
    assert github_deployments._installation_token(c) is None
