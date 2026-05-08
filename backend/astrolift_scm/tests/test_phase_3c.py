"""
SCM phase 3c — GitHub App token minting + webhook replay protection.

Network is patched out for the App-token tests; replay tests hit
the in-memory test database to assert the unique-per-(connection,
delivery_id) constraint.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection, WebhookDelivery
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


@pytest.fixture(autouse=True)
def _no_debug_toolbar(settings):
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DebugToolbar" not in m
    ]
    settings.DEBUG = False


@pytest.fixture
def app_install_connection():
    """A github_app_install connection with a real RSA private key
    encrypted in secret_ciphertext, so the JWT mint runs end-to-end
    even though the HTTP call is patched."""
    org = Organization.objects.create(name="Acme", slug="acme-app")
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    encrypted = encrypt_at_rest(pem)
    return SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        display_name="Acme App",
        oauth_client_id="123456",  # app_id reused on this column
        installation_id="987654321",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def test_app_install_token_caches_until_near_expiry(
    app_install_connection, monkeypatch
):
    """First call hits the network; second call within the cache
    window returns the cached token without a second request."""
    from astrolift_scm.providers.github_app import installation_token, _CACHE

    _CACHE.clear()  # isolate from previous tests in the same process

    call_count = {"n": 0}

    def fake_exchange(api_base, jwt_token, installation_id):
        call_count["n"] += 1
        return ("ghs_mocked_token_123", time.time() + 3600)

    monkeypatch.setattr(
        "astrolift_scm.providers.github_app._exchange_for_installation_token",
        fake_exchange,
    )

    a = installation_token(app_install_connection)
    b = installation_token(app_install_connection)
    assert a == b == "ghs_mocked_token_123"
    assert call_count["n"] == 1


def test_app_install_token_refreshes_when_within_margin(
    app_install_connection, monkeypatch
):
    """When the cached token is < refresh-margin seconds from
    expiry, a fresh exchange runs."""
    from astrolift_scm.providers import github_app

    github_app._CACHE.clear()
    sequence = iter(
        [
            ("token-1", time.time() + 60),  # expires inside the margin
            ("token-2", time.time() + 3600),
        ]
    )

    def fake_exchange(api_base, jwt_token, installation_id):
        return next(sequence)

    monkeypatch.setattr(
        "astrolift_scm.providers.github_app._exchange_for_installation_token",
        fake_exchange,
    )

    first = github_app.installation_token(app_install_connection)
    second = github_app.installation_token(app_install_connection)
    assert first == "token-1"
    assert second == "token-2"


def test_app_install_token_rejects_incomplete_config(monkeypatch):
    """Connection missing app_id or installation_id surfaces
    a recoverable error so the UI can render 'reconfigure'."""
    from astrolift_scm.providers.github_app import (
        GithubAppError,
        installation_token,
    )

    org = Organization.objects.create(name="Acme", slug="acme-incomplete")
    conn = SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        display_name="Acme App",
        oauth_client_id="",
        installation_id="",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"",
        is_active=True,
    )
    with pytest.raises(GithubAppError) as exc:
        installation_token(conn)
    assert exc.value.code == "INCOMPLETE_CONFIG"
    assert exc.value.recoverable is True


# ---------------------------------------------------------------------------
# Replay protection — receiver dedupes on (connection, delivery_id)
# ---------------------------------------------------------------------------


@pytest.fixture
def push_stack():
    org = Organization.objects.create(name="Acme", slug="acme-push3c")
    team = Team.objects.create(organization=org, name="Eng", slug="eng3c")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug="demo3c"
    )
    plugin = ProviderPlugin(
        name="P3c", slug="p-3c", version="0.0.1",
        capabilities_manifest={}, config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    cluster = TenantCluster.objects.create(
        organization=org, name="c3c", slug="c-3c",
        provider_plugin=ProviderPlugin.objects.get(slug="p-3c"),
        provider_config={}, endpoint="https://c", auth_method="kubeconfig",
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org, project=project, team=team,
        name="H3c", slug="hello-3c",
        provisioning_status="ready",
        source_kind="github",
        source_repo="acme-org/hello",
        deploy_branch="main",
        trigger_mode=RegisteredApp.TriggerMode.AUTO_ON_PUSH.value,
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://hello.example",
        required_approvals=0,
    )
    secret = "whsec_phase3c_value"
    encrypted = encrypt_at_rest(secret.encode("utf-8"))
    conn = SourceConnection.objects.create(
        organization=org,
        kind="github_pat",
        display_name="acme",
        account_login="acme-org",
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"",
        webhook_secret_backend_kind=encrypted.backend_kind,
        webhook_secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )
    return {"org": org, "app": app, "conn": conn, "secret": secret}


def _push_payload(repo: str, branch: str = "main", sha: str = "abc123"):
    return json.dumps(
        {
            "ref": f"refs/heads/{branch}",
            "after": sha,
            "repository": {"full_name": repo},
        }
    ).encode("utf-8")


def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


def test_replay_with_same_delivery_id_is_ignored(push_stack, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    body = _push_payload("acme-org/hello", sha="aaa111")
    sig = _sig(push_stack["secret"], body)
    delivery_id = "11111111-1111-1111-1111-111111111111"
    client = Client()

    first = client.post(
        f"/app/auth1/scm/github/webhook/{push_stack['conn'].guid}/",
        data=body, content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sig,
        HTTP_X_GITHUB_DELIVERY=delivery_id,
        HTTP_X_GITHUB_EVENT="push",
    )
    assert first.status_code == 202
    assert first.json()["fired"]
    assert WebhookDelivery.objects.count() == 1
    assert Deployment.objects.count() == 1

    # Resending the exact same payload + delivery_id must not fire again.
    second = client.post(
        f"/app/auth1/scm/github/webhook/{push_stack['conn'].guid}/",
        data=body, content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sig,
        HTTP_X_GITHUB_DELIVERY=delivery_id,
        HTTP_X_GITHUB_EVENT="push",
    )
    assert second.status_code == 202
    assert second.json()["ignored"] == "duplicate"
    assert WebhookDelivery.objects.count() == 1
    assert Deployment.objects.count() == 1


def test_different_delivery_ids_both_fire(push_stack, settings):
    """Two distinct delivery_ids → two deployments. Confirms the
    dedupe is per-delivery-id, not per-payload."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    body = _push_payload("acme-org/hello", sha="aaa111")
    sig = _sig(push_stack["secret"], body)
    client = Client()

    for delivery in ("aaa", "bbb"):
        resp = client.post(
            f"/app/auth1/scm/github/webhook/{push_stack['conn'].guid}/",
            data=body, content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=sig,
            HTTP_X_GITHUB_DELIVERY=delivery,
            HTTP_X_GITHUB_EVENT="push",
        )
        assert resp.status_code == 202
        assert resp.json()["fired"]

    assert WebhookDelivery.objects.count() == 2
    assert Deployment.objects.count() == 2


def test_delivery_row_links_to_triggered_deployment(push_stack, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    body = _push_payload("acme-org/hello", sha="zzz999")
    sig = _sig(push_stack["secret"], body)
    client = Client()
    resp = client.post(
        f"/app/auth1/scm/github/webhook/{push_stack['conn'].guid}/",
        data=body, content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sig,
        HTTP_X_GITHUB_DELIVERY="link-test",
        HTTP_X_GITHUB_EVENT="push",
    )
    assert resp.status_code == 202

    row = WebhookDelivery.objects.get(delivery_id="link-test")
    assert row.triggered_deployment is not None
    assert row.triggered_deployment.image_tag == "zzz999"
    assert row.repo_full_name == "acme-org/hello"
    assert row.branch == "main"
    assert row.head_sha == "zzz999"
    assert row.host_event == "push"
