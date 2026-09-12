"""A source webhook must not be installed against an address the host
cannot reach (#1693).

``PLATFORM_API_URL`` falls back to ``FRONTEND_URL``, which falls back to
``http://localhost:3000``. On a deployed install that set neither, the
per-repo hook registered cleanly against a loopback URL: autowire
reported success for every step, ``astro ci setup`` printed the loopback
address as the receiver, and no push ever triggered a deploy.

The refusal is deliberately narrow. A ``github_app_install`` connection
does not use the per-app receiver path at all -- its deliveries ride the
App's own webhook, addressed at ``APP_BASE_URL`` -- so guarding that path
would refuse an install that is already wired.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.webhooks import (
    install_astrolift_source_webhook,
    receiver_url_is_deliverable,
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
def app():
    org = Organization.objects.create(name="Acme", slug="acme-1693")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-1693")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="Web",
        slug="web-1693",
        source_kind="github",
        source_repo="acme/web",
    )


def _connection(app, kind: str) -> SourceConnection:
    encrypted = encrypt_at_rest(b"secret-material")
    return SourceConnection.objects.create(
        organization=app.organization,
        kind=kind,
        account_login="acme",
        installation_id="987" if kind == "github_app_install" else "",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:3000/api/webhooks/github/x/",
        "http://127.0.0.1:8000/api/webhooks/github/x/",
        "http://0.0.0.0:8000/api/webhooks/github/x/",
        "http://[::1]:8000/api/webhooks/github/x/",
        "/api/webhooks/github/x/",
        "",
        "ftp://example.com/api/webhooks/github/x/",
    ],
)
def test_undeliverable_addresses_are_rejected(url):
    assert receiver_url_is_deliverable(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://astrolift.example.com/api/webhooks/github/x/",
        "http://astrolift.internal.example/api/webhooks/github/x/",
    ],
)
def test_a_public_address_is_accepted(url):
    assert receiver_url_is_deliverable(url) is True


def test_install_refuses_a_loopback_receiver(app, settings):
    """The bug: this returned a wired-looking hook against localhost."""

    settings.PLATFORM_API_URL = "http://localhost:3000"
    _connection(app, "github_pat")

    result = install_astrolift_source_webhook(app)

    assert result.status == "no_public_url"
    assert "PLATFORM_API_URL" in result.error
    app.refresh_from_db()
    assert app.source_webhook_installed_at is None


def test_refusal_happens_before_the_host_is_called(app, settings, monkeypatch):
    """No hook is minted, so there is nothing to clean up on the host."""

    settings.PLATFORM_API_URL = "http://localhost:3000"
    _connection(app, "github_pat")

    def _boom(*args, **kwargs):
        raise AssertionError("install_webhook must not be called")

    monkeypatch.setattr("astrolift_scm.services.webhooks.install_webhook", _boom)

    assert install_astrolift_source_webhook(app).status == "no_public_url"


def test_an_app_install_connection_is_not_refused(app, settings, monkeypatch):
    """Its deliveries never use the per-app receiver path."""

    settings.PLATFORM_API_URL = "http://localhost:3000"
    settings.APP_BASE_URL = "https://astrolift.example.com"
    connection = _connection(app, "github_app_install")

    from astrolift_scm.providers import ProviderError

    def _app_delivers(*args, **kwargs):
        raise ProviderError(code="APP_DELIVERS", message="the App already delivers")

    monkeypatch.setattr("astrolift_scm.services.webhooks.install_webhook", _app_delivers)

    result = install_astrolift_source_webhook(app)

    assert result.status == "app_delivers"
    assert result.hook_id == ""
    # The address deliveries actually land on, not the per-app path that
    # this connection kind never uses.
    assert result.receiver_url == (
        f"https://astrolift.example.com/app/auth1/scm/github/webhook/{connection.guid}/"
    )
