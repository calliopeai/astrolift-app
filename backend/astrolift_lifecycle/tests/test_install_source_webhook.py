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


def test_install_creates_hook_and_persists_app_state(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
    settings,
):
    """Happy path: POST /hooks 201s → mutation returns ``status=created``
    + the host-side id, and the RegisteredApp row is updated with the
    hook id and ``installed_at`` timestamp."""
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
    # Receiver URL is keyed off the app's GUID so deliveries route
    # without grepping the payload.
    expected_url = f"https://api.astrolift.example.com/api/webhooks/github/{app_with_repo.guid}/"
    assert result.data.receiver_url == expected_url

    # POSTed to the right endpoint with the right config shape.
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/repos/acme/api/hooks")
    assert captured["body"]["config"]["url"] == expected_url
    assert captured["body"]["config"]["content_type"] == "json"
    assert "push" in captured["body"]["events"]
    assert "pull_request" in captured["body"]["events"]
    assert captured["body"]["config"]["secret"]  # non-empty HMAC secret

    # Persistence: app row now carries the hook id + timestamp.
    app_with_repo.refresh_from_db()
    assert app_with_repo.source_webhook_id == "4242"
    assert app_with_repo.source_webhook_installed_at is not None


# ---------------------------------------------------------------------------
# Refresh path
# ---------------------------------------------------------------------------


def test_install_refresh_path_rotates_secret_when_hook_exists(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
    settings,
):
    """GitHub 422 with "Hook already exists" → mutation returns
    ``status=refreshed``, rotates the connection's stored secret,
    and advances the app's ``installed_at`` timestamp."""
    permission_resolver.grant(Permission.APP_UPDATE)
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com"

    # Pre-seed: imagine a prior install left a hook id on the row.
    app_with_repo.source_webhook_id = "1111"
    app_with_repo.save(update_fields=["source_webhook_id", "updated_at", "version"])

    def fake_urlopen(req, timeout=15):
        raise _http_error(
            422,
            b'{"message":"Validation Failed","errors":[{"message":"Hook already exists on this repository"}]}',
            req.full_url,
        )

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
    assert result.data.status == "refreshed"
    assert result.data.hook_id == "1111"  # prior id preserved

    # The connection now carries a freshly-minted webhook secret.
    github_connection.refresh_from_db()
    assert github_connection.webhook_secret_ciphertext

    # ``installed_at`` advanced.
    app_with_repo.refresh_from_db()
    assert app_with_repo.source_webhook_installed_at is not None


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
# GitLab gap
# ---------------------------------------------------------------------------


def test_install_gitlab_source_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """A GitLab-sourced app surfaces a clean PRECONDITION (not a
    500) explaining the GitHub-only scope."""
    permission_resolver.grant(Permission.APP_UPDATE)
    app_with_repo.source_kind = "gitlab"
    app_with_repo.save(update_fields=["source_kind", "updated_at", "version"])

    def boom(*a, **kw):
        raise AssertionError("must not call GitHub for a GitLab-sourced app")

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
    assert "github" in result.errors[0].message.lower()


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
