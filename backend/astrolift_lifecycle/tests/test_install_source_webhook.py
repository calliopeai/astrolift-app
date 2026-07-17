"""Tests for ``installAstroliftSourceWebhook`` (#385).

Covers the mutation end-to-end: the GitHub POST /hooks call is
stubbed at ``urllib.request.urlopen``; everything above the wire
(connection picker, permission gate, error mapping, persistence on
``RegisteredApp.source_webhook_*``) runs against real Postgres rows.
"""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_lifecycle.schema.mutations import (
    InstallSourceWebhookInput,
    LifecycleMutation,
)
from astrolift_scm.models import SourceConnection
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


def _info(user=None):
    request = SimpleNamespace(user=user)
    context = SimpleNamespace(request=request, user=user)
    return SimpleNamespace(context=context)


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


@pytest.fixture
def app_with_repo(app):
    """The base ``app`` fixture has no source repo. The webhook install
    needs one to compute the host-side hook URL."""
    app.source_kind = "github"
    app.source_repo = "acme/api"
    app.default_branch = "main"
    app.save(
        update_fields=[
            "source_kind",
            "source_repo",
            "default_branch",
            "updated_at",
            "version",
        ]
    )
    return app


@pytest.fixture
def github_connection(org):
    encrypted = encrypt_at_rest(b"gho_test_token_never_hits_the_network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name="GitHub: alice",
        account_login="alice",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


class _Response201:
    """Mimics urlopen's success shape for POST /repos/.../hooks,
    which returns 201 with a JSON body containing ``id``."""

    def __init__(self, hook_id: int = 4242):
        self._body = json.dumps({"id": hook_id, "active": True}).encode("utf-8")

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body, status=201)

    def __exit__(self, *_):
        return False


def _http_error(code: int, body: bytes = b"", url: str = "https://example") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_install_github_org_oauth_user_creates_hook(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
    settings,
):
    """Webhook install is the documented exception to "platform writes are
    App-only": it uses the broad org-level picker (App > OAuth-user > PAT)
    because the webhook-secret lifecycle is a closed loop over ONE
    connection (install mints the secret on the picked row; ingest re-picks
    it to HMAC-verify; teardown re-picks it to delete). The picker is
    ORG-scoped — it never filters to the requesting viewer — so an
    org-level ``github_oauth_user`` connection legitimately drives the
    install and lands a real per-repo hook (``status=created``). The
    invariant this respects is "no VIEWER-scoped personal token drives a
    platform write"; an org connection is not viewer-scoped."""
    permission_resolver.grant(Permission.APP_UPDATE)
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com"

    captured: dict = {}

    def fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Response201(hook_id=4242)

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    with _ctx(org):
        result = LifecycleMutation().install_astrolift_source_webhook(
            _info(),
            input=InstallSourceWebhookInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.status == "created"
    assert result.data.hook_id == "4242"
    # POSTed to the repo hooks endpoint under the org oauth-user token.
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/repos/acme/api/hooks")
    assert captured["body"]["config"]["secret"]  # non-empty HMAC secret

    # Persistence: app row now carries the real hook id + timestamp.
    app_with_repo.refresh_from_db()
    assert app_with_repo.source_webhook_id == "4242"
    assert app_with_repo.source_webhook_installed_at is not None


# ---------------------------------------------------------------------------
# Real per-repo-hook create + persistence (GitLab: the hosts with no
# App-install analogue still create an actual hook and return a real
# hook_id, unlike the GitHub App path which only ever verifies coverage).
# ---------------------------------------------------------------------------


def test_install_creates_real_hook_and_persists_app_state(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    settings,
):
    """A host that genuinely creates a per-repo hook (GitLab here) returns
    ``status=created`` with the real host-side id, and the RegisteredApp
    row is updated with that id + the ``installed_at`` timestamp. This is
    the path that must keep a truthful, non-empty ``hook_id`` — distinct
    from the GitHub App ``app_delivers`` path where no hook exists."""
    permission_resolver.grant(Permission.APP_UPDATE)
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com"

    app_with_repo.source_kind = "gitlab"
    app_with_repo.save(update_fields=["source_kind", "updated_at", "version"])

    encrypted = encrypt_at_rest(b"glpat_test_token")
    SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITLAB_PAT,
        display_name="GitLab PAT",
        account_login="acme",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        lambda req, timeout=15: _GitlabCreate(req),
    )

    with _ctx(org):
        result = LifecycleMutation().install_astrolift_source_webhook(
            _info(),
            input=InstallSourceWebhookInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.status == "created"
    assert result.data.hook_id == "4242"  # a REAL, non-empty hook id

    # Persistence: app row now carries the real hook id + timestamp.
    app_with_repo.refresh_from_db()
    assert app_with_repo.source_webhook_id == "4242"
    assert app_with_repo.source_webhook_installed_at is not None


class _GitlabCreate:
    """urlopen success shape for POST /projects/:id/hooks (201 + id)."""

    def __init__(self, req):
        self._body = json.dumps({"id": 4242, "url": req.full_url, "active": True}).encode("utf-8")

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body, status=201)

    def __exit__(self, *_):
        return False


# ---------------------------------------------------------------------------
# No-connection path
# ---------------------------------------------------------------------------


def test_install_no_connection_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
):
    """No SourceConnection in the app's org → PRECONDITION envelope
    with a "connect a GitHub identity" hint."""
    permission_resolver.grant(Permission.APP_UPDATE)

    def boom(*a, **kw):
        raise AssertionError("must not reach the network without a connection")

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        boom,
    )

    with _ctx(org):
        result = LifecycleMutation().install_astrolift_source_webhook(
            _info(),
            input=InstallSourceWebhookInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "connection" in result.errors[0].message.lower()


# ---------------------------------------------------------------------------
# GitLab branch (#533)
# ---------------------------------------------------------------------------


def test_install_gitlab_source_creates_hook(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    settings,
):
    """GitLab-sourced app installs a hook against the GitLab projects
    hooks API. Same envelope shape as the GitHub branch."""
    permission_resolver.grant(Permission.APP_UPDATE)
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com"

    app_with_repo.source_kind = "gitlab"
    app_with_repo.save(update_fields=["source_kind", "updated_at", "version"])

    # GitLab OAuth-user connection. The provider's _token() decrypts a
    # bearer token from secret_ciphertext; matches the shape used by
    # the SCM tests for GitLab.
    encrypted = encrypt_at_rest(b"glpat_test_token")
    SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITLAB_OAUTH_USER,
        display_name="GitLab: alice",
        account_login="alice",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )

    captured: dict = {}

    class _CM:
        def __init__(self, req):
            self._body = json.dumps({"id": 7777, "url": req.full_url, "active": True}).encode("utf-8")
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["body"] = json.loads(req.data.decode("utf-8"))

        def __enter__(self):
            return SimpleNamespace(read=lambda: self._body, status=201)

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        lambda req, timeout=15: _CM(req),
    )

    with _ctx(org):
        result = LifecycleMutation().install_astrolift_source_webhook(
            _info(),
            input=InstallSourceWebhookInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.status == "created"
    assert result.data.hook_id == "7777"
    expected_url = f"https://api.astrolift.example.com/api/webhooks/gitlab/{app_with_repo.guid}/"
    assert result.data.receiver_url == expected_url
    # Routed via the GitLab projects/hooks endpoint with the right
    # event flags.
    assert "/api/v4/projects/" in captured["url"]
    assert captured["url"].endswith("/hooks")
    assert captured["method"] == "POST"
    assert captured["body"]["push_events"] is True
    # HMAC secret carried through as the ``token`` field (GitLab's
    # naming) and persisted on the connection's webhook_secret column.
    assert captured["body"]["token"]


def test_install_bitbucket_source_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """A Bitbucket-sourced app still surfaces a clean PRECONDITION until
    the Bitbucket driver lands. Sibling of the old GitLab gap test."""
    permission_resolver.grant(Permission.APP_UPDATE)
    app_with_repo.source_kind = "bitbucket"
    app_with_repo.save(update_fields=["source_kind", "updated_at", "version"])

    def boom(*a, **kw):
        raise AssertionError("must not call any provider for a bitbucket-sourced app")

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        boom,
    )
    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        boom,
    )

    with _ctx(org):
        result = LifecycleMutation().install_astrolift_source_webhook(
            _info(),
            input=InstallSourceWebhookInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "bitbucket" in result.errors[0].message.lower()


# ---------------------------------------------------------------------------
# GitHub-App-install branch: the App's own webhook delivers, but only
# for the repos the operator installed it on. The step must tell the
# truth instead of returning a fake success with an empty hook id.
# ---------------------------------------------------------------------------


@pytest.fixture
def github_app_connection(org):
    """An org-level GitHub App install connection (user FK NULL). Ranks
    ahead of any OAuth-user connection in the picker."""
    encrypted = encrypt_at_rest(b"fake-pem-bytes")
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


class _InstallationReposResponse:
    """Mimics urlopen's success shape for GET /installation/repositories:
    ``{ total_count, repositories: [...] }``."""

    def __init__(self, full_names: list[str]):
        repos = [{"full_name": fn, "name": fn.split("/")[-1]} for fn in full_names]
        self._body = json.dumps({"total_count": len(repos), "repositories": repos}).encode("utf-8")

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body, status=200)

    def __exit__(self, *_):
        return False


def test_install_app_delivers_when_repo_in_installation_set(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_app_connection,
    settings,
):
    """github_app_install + the App IS installed on the target repo →
    ``status=app_delivers`` with an EMPTY hook_id (honest: no per-repo
    hook exists), and ``installed_at`` is recorded."""
    permission_resolver.grant(Permission.APP_UPDATE)
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com"

    # Short-circuit the App-JWT → installation-token exchange so the
    # membership check reaches the (mocked) /installation/repositories
    # call with a usable Bearer token.
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token",
        lambda connection: "ghs_fake_installation_token",
    )

    captured: dict = {}

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        # The App is installed on acme/api → membership confirmed.
        return _InstallationReposResponse(["acme/api", "acme/other"])

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    with _ctx(org):
        result = LifecycleMutation().install_astrolift_source_webhook(
            _info(),
            input=InstallSourceWebhookInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.status == "app_delivers"
    # The empty hook_id is the truth — the App's org webhook delivers,
    # no per-repo hook was created. It must NOT masquerade as a hook.
    assert result.data.hook_id == ""
    # Membership was verified against the installation's repo set with
    # the App's Bearer installation token (not a user "token" header).
    assert "/installation/repositories" in captured["url"]
    assert captured["auth"] == "Bearer ghs_fake_installation_token"

    # The app is genuinely wired → installed_at recorded, and no fake
    # per-repo hook id was persisted.
    app_with_repo.refresh_from_db()
    assert app_with_repo.source_webhook_installed_at is not None
    assert app_with_repo.source_webhook_id in ("", None)


def test_install_app_not_installed_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_app_connection,
    settings,
):
    """github_app_install but the App is NOT installed on the target repo
    → a real PRECONDITION failure (not a fake success), and the app's
    ``installed_at`` is left untouched."""
    permission_resolver.grant(Permission.APP_UPDATE)
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com"

    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token",
        lambda connection: "ghs_fake_installation_token",
    )

    def fake_urlopen(req, timeout=10):
        # The installation covers other repos, but NOT acme/api.
        return _InstallationReposResponse(["acme/other", "acme/unrelated"])

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    with _ctx(org):
        result = LifecycleMutation().install_astrolift_source_webhook(
            _info(),
            input=InstallSourceWebhookInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "not installed" in result.errors[0].message.lower()

    # No fake success side effects: the app was NOT marked installed.
    app_with_repo.refresh_from_db()
    assert app_with_repo.source_webhook_installed_at is None
    assert app_with_repo.source_webhook_id in ("", None)


# ---------------------------------------------------------------------------
# Permission gate
# ---------------------------------------------------------------------------


def test_install_permission_denied_without_app_update(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """No APP_UPDATE grant → PERMISSION_DENIED, never touches the host."""

    def boom(*a, **kw):
        raise AssertionError("must not reach the network without permission")

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        boom,
    )

    with _ctx(org):
        result = LifecycleMutation().install_astrolift_source_webhook(
            _info(),
            input=InstallSourceWebhookInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_install_app_not_found(monkeypatch, permission_resolver, org):
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = LifecycleMutation().install_astrolift_source_webhook(
            _info(),
            input=InstallSourceWebhookInput(app_slug="no-such-app"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"
