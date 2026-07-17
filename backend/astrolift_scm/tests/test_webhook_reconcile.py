"""Tests for phantom source-webhook reconciliation (#1108).

``reconcile_source_webhook_state`` is the truthful check behind the autowire
repair: a RegisteredApp can carry ``source_webhook_installed_at`` with nothing
on the host actually delivering pushes (found live on pickup-windows-tool —
``installed_at`` set, ``source_webhook_id`` empty, zero webhooks on the repo,
App not installed). These tests pin the detect-and-repair matrix; the GitHub
GET calls are stubbed at ``urllib.request.urlopen`` while everything above the
wire (connection pick, marker clears) runs against real Postgres rows.
"""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.webhooks import reconcile_source_webhook_state
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
    return Organization.objects.create(name="Acme", slug="acme-recon")


def _make_app(org, *, webhook_id="", installed=True, repo="acme/api", kind="github"):
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{repo.replace('/', '-')}")
    project = Project.objects.create(
        organization=org, team=team, name="P", slug=f"p-{repo.replace('/', '-')}"
    )
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug=f"app-{repo.replace('/', '-')}",
        source_kind=kind,
        source_repo=repo,
        source_webhook_id=webhook_id,
        source_webhook_installed_at=timezone.now() if installed else None,
    )


def _oauth_conn(org):
    encrypted = encrypt_at_rest(b"gho_token")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name="GitHub: alice",
        account_login="alice",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _app_conn(org):
    encrypted = encrypt_at_rest(b"fake-pem")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name="GitHub App: acme",
        account_login="acme",
        installation_id="987",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


class _Resp:
    def __init__(self, body: bytes, status: int = 200):
        self._body, self._status = body, status

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body, status=self._status)

    def __exit__(self, *_):
        return False


def _installation_repos(full_names: list[str]) -> _Resp:
    repos = [{"full_name": fn, "name": fn.split("/")[-1]} for fn in full_names]
    return _Resp(json.dumps({"total_count": len(repos), "repositories": repos}).encode())


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com", code, "err", {}, io.BytesIO(b""))


# ---------------------------------------------------------------------------
# The reported phantom: installed_at set, id empty, per-repo connection.
# No host round-trip is needed — a per-repo connection with an empty id is a
# failed install that advanced the timestamp anyway.
# ---------------------------------------------------------------------------


def test_empty_id_under_per_repo_connection_is_phantom(monkeypatch, org):
    app = _make_app(org, webhook_id="", installed=True)
    _oauth_conn(org)

    def boom(*a, **k):
        raise AssertionError("empty-id phantom is structural; must not hit the network")

    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", boom)

    status = reconcile_source_webhook_state(app)

    assert status == "repaired_phantom"
    app.refresh_from_db()
    assert app.source_webhook_installed_at is None
    assert app.source_webhook_id == ""


def test_no_connection_but_marker_set_is_phantom(monkeypatch, org):
    """installed_at set with NO connection at all cannot be delivering."""
    app = _make_app(org, webhook_id="", installed=True)

    status = reconcile_source_webhook_state(app)

    assert status == "repaired_phantom"
    app.refresh_from_db()
    assert app.source_webhook_installed_at is None


# ---------------------------------------------------------------------------
# GitHub-App connection: honest only if the App covers the repo.
# ---------------------------------------------------------------------------


def test_app_install_not_covering_repo_is_phantom(monkeypatch, org):
    app = _make_app(org, webhook_id="", installed=True)
    _app_conn(org)
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token",
        lambda conn: "ghs_fake",
    )
    # Installation covers other repos, but NOT acme/api.
    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        lambda req, timeout=10: _installation_repos(["acme/other"]),
    )

    status = reconcile_source_webhook_state(app)

    assert status == "repaired_phantom"
    app.refresh_from_db()
    assert app.source_webhook_installed_at is None


def test_app_install_covering_repo_is_app_delivers(monkeypatch, org):
    app = _make_app(org, webhook_id="", installed=True)
    _app_conn(org)
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token",
        lambda conn: "ghs_fake",
    )
    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        lambda req, timeout=10: _installation_repos(["acme/api", "acme/other"]),
    )

    status = reconcile_source_webhook_state(app)

    assert status == "app_delivers"
    app.refresh_from_db()
    # Genuinely wired — the marker is truthful and must NOT be cleared.
    assert app.source_webhook_installed_at is not None


# ---------------------------------------------------------------------------
# Per-repo hook with a recorded id: verify it still exists on the host.
# ---------------------------------------------------------------------------


def test_recorded_hook_deleted_on_host_is_repaired(monkeypatch, org):
    app = _make_app(org, webhook_id="4242", installed=True)
    _oauth_conn(org)

    def fake_urlopen(req, timeout=10):
        # GET /repos/acme/api/hooks/4242 → 404 (hook was deleted on GitHub).
        raise _http_error(404)

    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", fake_urlopen)

    status = reconcile_source_webhook_state(app)

    assert status == "repaired_missing"
    app.refresh_from_db()
    assert app.source_webhook_id == ""
    assert app.source_webhook_installed_at is None


def test_recorded_hook_still_live_is_kept(monkeypatch, org):
    app = _make_app(org, webhook_id="4242", installed=True)
    _oauth_conn(org)
    # GET hooks/4242 → 200: the hook is live.
    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        lambda req, timeout=10: _Resp(json.dumps({"id": 4242, "active": True}).encode()),
    )

    status = reconcile_source_webhook_state(app)

    assert status == "live"
    app.refresh_from_db()
    assert app.source_webhook_id == "4242"
    assert app.source_webhook_installed_at is not None


# ---------------------------------------------------------------------------
# No marker at all — nothing to reconcile.
# ---------------------------------------------------------------------------


def test_no_marker_is_noop(monkeypatch, org):
    app = _make_app(org, webhook_id="", installed=False)

    def boom(*a, **k):
        raise AssertionError("no marker → no host call")

    monkeypatch.setattr("astrolift_scm.providers.github.urllib.request.urlopen", boom)

    assert reconcile_source_webhook_state(app) == "no_marker"


def test_gitlab_recorded_hook_unverifiable_is_kept(monkeypatch, org):
    """GitLab has no existence checker yet — a recorded hook is LEFT alone
    (conservative) rather than cleared on an unverifiable host."""
    app = _make_app(org, webhook_id="7777", installed=True, repo="acme/gl", kind="gitlab")
    encrypted = encrypt_at_rest(b"glpat")
    SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITLAB_PAT,
        display_name="GitLab PAT",
        account_login="acme",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )

    status = reconcile_source_webhook_state(app)

    assert status == "unverifiable"
    app.refresh_from_db()
    assert app.source_webhook_id == "7777"
    assert app.source_webhook_installed_at is not None
