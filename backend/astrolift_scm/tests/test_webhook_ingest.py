"""
Push-webhook receiver: HMAC verification + DeployAppWorkflow trigger.

The Temporal client is patched out (``no_temporal``), so tests don't
need a Temporal server — we only assert that the receiver got far
enough to call ``start_workflow`` (or didn't, in negative cases).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
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
    """The dev environment runs django-debug-toolbar; its
    middleware tries to reverse 'djdt:render_panel' on every
    response and 500s when the toolbar URL include isn't loaded
    (which it isn't, under the test runner). Strip it for the
    duration of the test so the receiver's actual responses get
    through to the assertions."""
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if "DebugToolbar" not in m
    ]
    settings.DEBUG = False


@pytest.fixture
def stack():
    org = Organization.objects.create(name="Acme", slug="acme-wh")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug="demo"
    )
    plugin = ProviderPlugin(
        name="P", slug="p-wh", version="0.0.1",
        capabilities_manifest={}, config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    cluster = TenantCluster.objects.create(
        organization=org, name="c", slug="c-wh",
        provider_plugin=ProviderPlugin.objects.get(slug="p-wh"),
        provider_config={}, endpoint="https://c", auth_method="kubeconfig",
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org, project=project, team=team,
        name="Hello", slug="hello-wh",
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

    secret = "whsec_test_value_123"
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


def _github_sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


def _push_payload(repo: str, branch: str = "main", sha: str = "abc123"):
    return json.dumps(
        {
            "ref": f"refs/heads/{branch}",
            "after": sha,
            "repository": {"full_name": repo},
        }
    ).encode("utf-8")


def test_github_push_with_valid_hmac_fires_deploy(stack, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    body = _push_payload("acme-org/hello")
    sig = _github_sig(stack["secret"], body)

    client = Client()
    resp = client.post(
        f"/app/auth1/scm/github/webhook/{stack['conn'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sig,
    )
    assert resp.status_code == 202, resp.content
    payload = resp.json()
    assert payload["ok"] is True
    assert len(payload["fired"]) == 1
    assert payload["fired"][0]["app"] == "hello-wh"

    # The deployment row landed.
    deploy = Deployment.objects.get(registered_app=stack["app"])
    assert deploy.trigger_kind == Deployment.TriggerKind.PUSH.value
    assert deploy.image_tag == "abc123"


def test_github_push_with_bad_hmac_401s(stack):
    body = _push_payload("acme-org/hello")
    client = Client()
    resp = client.post(
        f"/app/auth1/scm/github/webhook/{stack['conn'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256="sha256=" + "0" * 64,
    )
    assert resp.status_code == 401
    assert Deployment.objects.count() == 0


def test_github_push_no_signature_401s(stack):
    body = _push_payload("acme-org/hello")
    client = Client()
    resp = client.post(
        f"/app/auth1/scm/github/webhook/{stack['conn'].guid}/",
        data=body,
        content_type="application/json",
    )
    assert resp.status_code == 401


def test_github_push_unknown_connection_404s(stack):
    body = _push_payload("acme-org/hello")
    client = Client()
    resp = client.post(
        "/app/auth1/scm/github/webhook/00000000-0000-0000-0000-000000000000/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_github_sig(stack["secret"], body),
    )
    assert resp.status_code == 404


def test_github_push_to_non_deploy_branch_ignored(stack, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    body = _push_payload("acme-org/hello", branch="feature/x", sha="def456")
    sig = _github_sig(stack["secret"], body)

    client = Client()
    resp = client.post(
        f"/app/auth1/scm/github/webhook/{stack['conn'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sig,
    )
    assert resp.status_code == 202
    assert resp.json()["fired"] == []
    assert Deployment.objects.count() == 0


def test_github_push_unknown_repo_ignored(stack, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    body = _push_payload("acme-org/different-repo")
    sig = _github_sig(stack["secret"], body)

    client = Client()
    resp = client.post(
        f"/app/auth1/scm/github/webhook/{stack['conn'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sig,
    )
    assert resp.status_code == 202
    assert resp.json()["ignored"] == "no_matching_app"


def test_github_non_push_event_acked(stack):
    body = json.dumps({"zen": "ping"}).encode("utf-8")
    sig = _github_sig(stack["secret"], body)
    client = Client()
    resp = client.post(
        f"/app/auth1/scm/github/webhook/{stack['conn'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sig,
    )
    assert resp.status_code == 202
    assert resp.json()["ignored"] == "non_push"


def test_gitlab_push_with_valid_token_fires_deploy(stack, settings):
    """GitLab uses a plaintext token in X-Gitlab-Token, not HMAC."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    # Repurpose the same connection but flip the kind so the gitlab
    # endpoint will accept it.
    stack["conn"].kind = "gitlab_pat"
    stack["conn"].save()
    body = json.dumps(
        {
            "object_kind": "push",
            "ref": "refs/heads/main",
            "after": "abc123",
            "project": {"path_with_namespace": "acme-org/hello"},
        }
    ).encode("utf-8")
    client = Client()
    resp = client.post(
        f"/app/auth1/scm/gitlab/webhook/{stack['conn'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_GITLAB_TOKEN=stack["secret"],
    )
    assert resp.status_code == 202, resp.content
    assert resp.json()["fired"]


def test_gitlab_push_with_wrong_token_401s(stack):
    stack["conn"].kind = "gitlab_pat"
    stack["conn"].save()
    body = json.dumps(
        {
            "object_kind": "push",
            "ref": "refs/heads/main",
            "after": "abc",
            "project": {"path_with_namespace": "acme-org/hello"},
        }
    ).encode("utf-8")
    client = Client()
    resp = client.post(
        f"/app/auth1/scm/gitlab/webhook/{stack['conn'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_GITLAB_TOKEN="wrong-token",
    )
    assert resp.status_code == 401
