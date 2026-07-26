"""Per-org attribution of shared-GitHub-App webhook deliveries (#1123).

Under ``GITHUB_APP_CONNECTION_SCOPE=per_install`` one GitHub App is shared
across every org in the install. A GitHub App has a SINGLE webhook URL
(fixed at App-create time to the canonical/creator org's connection guid),
so every org's installation delivers to that one receiver URL. The receiver
must attribute each delivery to the org that owns the *installation* (the
``installation.id`` in the payload), not the org that owns the receiver URL.

Two orgs share one App (same webhook secret + App ID) but hold distinct
installation ids and distinct repos (``registered_app_repo_manifest_unique``
makes ``(source_repo, manifest_path)`` globally unique, so a repo lives in
exactly one org). Every delivery below hits org A's canonical guid URL; the
installation id decides whose app deploys. Without attribution org B's
delivery is scoped to org A, its repo isn't found there, and the deploy is
silently dropped as ``no_matching_app``.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from django.test import Client

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from core.secrets import encrypt_at_rest

pytestmark = pytest.mark.django_db

# The App-level webhook secret. Shared across every org's connection under
# per_install (copied from the canonical App on reuse), so GitHub signs every
# installation's delivery with this one value.
SHARED_WEBHOOK_SECRET = "whsec_shared_app_secret_123"
# Numeric GitHub App ID — identical across orgs for a shared App.
SHARED_APP_ID = "3705068"


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
    """django-debug-toolbar middleware 500s under the test client (it
    reverses a URL the test URLconf doesn't mount). Strip it so the
    receiver's real responses reach the assertions."""
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False


def _make_org_app(
    *,
    label: str,
    installation_id: str,
    source_repo: str,
    plugin: ProviderPlugin,
    canonical: bool,
) -> dict:
    """Build one org with a github_app_install connection + an auto-deploy app.

    All orgs share ``SHARED_WEBHOOK_SECRET`` + ``SHARED_APP_ID`` (the App is
    shared under per_install) but carry distinct ``installation_id``s. The
    ``canonical`` flag marks the org whose connection guid is baked into the
    App's single webhook URL.
    """
    org = Organization.objects.create(name=f"Org {label}", slug=f"org-{label}-pi")
    team = Team.objects.create(organization=org, name="Eng", slug=f"team-{label}-pi")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug=f"proj-{label}-pi"
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name=f"cluster-{label}",
        slug=f"cluster-{label}-pi",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://c",
        auth_method="kubeconfig",
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name=f"App {label}",
        slug=f"app-{label}-pi",
        provisioning_status="ready",
        source_kind="github",
        source_repo=source_repo,
        deploy_branch="main",
        trigger_mode=RegisteredApp.TriggerMode.AUTO_ON_PUSH.value,
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url=f"https://{label}.example",
        required_approvals=0,
    )

    encrypted = encrypt_at_rest(SHARED_WEBHOOK_SECRET.encode("utf-8"))
    conn = SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name=f"GitHub App: shared-app ({label})",
        account_login=f"gh-{label}",
        installation_id=installation_id,
        oauth_client_id=SHARED_APP_ID,
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"",
        webhook_secret_backend_kind=encrypted.backend_kind,
        webhook_secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )
    return {"org": org, "app": app, "conn": conn, "repo": source_repo, "canonical": canonical}


@pytest.fixture
def per_install():
    """Two orgs sharing ONE GitHub App. Same secret + App ID; distinct
    installation ids and distinct repos. Org A is canonical (owns the
    webhook URL guid)."""
    plugin = ProviderPlugin(
        name="P", slug="plugin-pi", version="0.0.1", capabilities_manifest={}, config_schema={}
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="plugin-pi")

    org_a = _make_org_app(
        label="a",
        installation_id="1001",
        source_repo="acme-org/hello",
        plugin=plugin,
        canonical=True,
    )
    org_b = _make_org_app(
        label="b",
        installation_id="2002",
        source_repo="beta-org/service",
        plugin=plugin,
        canonical=False,
    )
    return {"a": org_a, "b": org_b}


def _github_sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _app_push(repo: str, installation_id: int, branch: str = "main", sha: str = "abc123") -> bytes:
    """A GitHub App push payload — carries the top-level ``installation.id``."""
    return json.dumps(
        {
            "ref": f"refs/heads/{branch}",
            "after": sha,
            "repository": {"full_name": repo},
            "installation": {"id": installation_id},
        }
    ).encode("utf-8")


def _post_to_canonical(per_install, body: bytes):
    """POST a delivery to org A's (canonical) webhook URL — the single URL a
    shared App is configured with, regardless of which org's repo pushed."""
    canonical_guid = per_install["a"]["conn"].guid
    client = Client()
    return client.post(
        f"/app/auth1/scm/github/webhook/{canonical_guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_github_sig(SHARED_WEBHOOK_SECRET, body),
    )


def test_delivery_attributes_to_owning_org_not_receiver_url_org(per_install, settings):
    """Org B's installation delivers to org A's canonical URL — the deploy
    must land on org B's app. Without attribution the delivery is scoped to
    org A (the URL's org), org B's repo isn't found there, and nothing fires.
    This is the core per_install attribution fix."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    body = _app_push(per_install["b"]["repo"], installation_id=2002)  # org B's installation

    resp = _post_to_canonical(per_install, body)

    assert resp.status_code == 202, resp.content
    assert [f["app"] for f in resp.json()["fired"]] == ["app-b-pi"]

    # Org B's app deployed; org A's did NOT (attribution followed the
    # installation id, not the receiver URL's org).
    assert Deployment.objects.filter(registered_app=per_install["b"]["app"]).count() == 1
    assert Deployment.objects.filter(registered_app=per_install["a"]["app"]).count() == 0


def test_canonical_org_installation_fires_its_own_app(per_install, settings):
    """Baseline: org A's own installation (which also owns the URL) fires org
    A's app and not org B's."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    body = _app_push(per_install["a"]["repo"], installation_id=1001)  # org A's installation

    resp = _post_to_canonical(per_install, body)

    assert resp.status_code == 202, resp.content
    assert [f["app"] for f in resp.json()["fired"]] == ["app-a-pi"]
    assert Deployment.objects.filter(registered_app=per_install["a"]["app"]).count() == 1
    assert Deployment.objects.filter(registered_app=per_install["b"]["app"]).count() == 0


def test_unknown_installation_ignored_cleanly(per_install, settings):
    """An installation id with no owning connection is acked-and-ignored
    (fail closed) — never processed under the receiver URL's org."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    body = _app_push(per_install["b"]["repo"], installation_id=9999)  # no connection

    resp = _post_to_canonical(per_install, body)

    assert resp.status_code == 202, resp.content
    assert resp.json()["ignored"] == "unknown_installation"
    # Nothing deployed for either org — fail closed, not fall back to org A.
    assert Deployment.objects.count() == 0


def test_bad_signature_still_401s_before_attribution(per_install):
    """A forged delivery is rejected at the HMAC boundary, before any
    installation attribution runs."""
    body = _app_push(per_install["b"]["repo"], installation_id=2002)
    client = Client()
    resp = client.post(
        f"/app/auth1/scm/github/webhook/{per_install['a']['conn'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256="sha256=" + "0" * 64,
    )
    assert resp.status_code == 401
    assert Deployment.objects.count() == 0
