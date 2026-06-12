"""GitHub PR webhook receiver — preview env dispatch (#778).

Covers the route ``POST /api/webhooks/github/<app_guid>/`` end-to-end:

* 404 when the app guid doesn't resolve.
* 401 when no webhook secret is configured on the picked connection.
* 401 on signature mismatch.
* 200 ack for non-pull_request event headers (ping / push / installation).
* 400 on malformed JSON.
* 200 ack for ignored actions (labeled / edited / …).
* BUILD_PREVIEW path for opened / synchronize:
    - PreviewEnvironment + AppEnvironment rows are created on first delivery.
    - A repeat delivery for the same PR reuses the existing rows
      (idempotent).
    - No default tenant cluster bound → 200 + "no default tenant cluster
      bound" reason rather than a 500.
* TEARDOWN_PREVIEW path for closed:
    - 200 + "no preview to tear down" when no preview exists.
    - Workflow_id surfaced when a preview is found.

Temporal is patched off (``ASTROLIFT_TEMPORAL_ENABLED=False``) so the
view's ``start_workflow`` call no-ops; the assertions cover the row
state that lands on the DB side.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from django.test import Client

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
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
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False


@pytest.fixture(autouse=True)
def _temporal_disabled(settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False


@pytest.fixture
def stack():
    org = Organization.objects.create(name="Acme", slug="acme-pr")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-pr")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-pr")
    plugin = ProviderPlugin(
        name="P",
        slug="p-pr",
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    cluster = TenantCluster.objects.create(
        organization=org,
        name="c",
        slug="c-pr",
        provider_plugin=ProviderPlugin.objects.get(slug="p-pr"),
        provider_config={},
        endpoint="https://c",
        auth_method="kubeconfig",
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Hello",
        slug="hello-pr",
        provisioning_status="ready",
        source_kind="github",
        source_repo="acme-org/hello",
        deploy_branch="main",
        trigger_mode=RegisteredApp.TriggerMode.AUTO_ON_PUSH.value,
        preview_enabled=True,
        default_tenant_cluster=cluster,
    )

    secret = "whsec_pr_value_456"
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
    return {"org": org, "app": app, "conn": conn, "secret": secret, "cluster": cluster}


def _github_sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _pr_payload(
    *,
    action: str = "opened",
    pr_number: int = 42,
    repo: str = "acme-org/hello",
    head_sha: str = "deadbeef00000000000000000000000000000000",
    head_ref: str = "feature/x",
    merged: bool = False,
    user_type: str = "User",
) -> bytes:
    return json.dumps(
        {
            "action": action,
            "repository": {"full_name": repo},
            "pull_request": {
                "number": pr_number,
                "merged": merged,
                "head": {"sha": head_sha, "ref": head_ref},
                "user": {"type": user_type},
            },
        }
    ).encode("utf-8")


def _post(client: Client, app_guid: str, *, body: bytes, secret: str, event: str = "pull_request"):
    return client.post(
        f"/api/webhooks/github/{app_guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_github_sig(secret, body),
        HTTP_X_GITHUB_EVENT=event,
        HTTP_X_GITHUB_DELIVERY="11111111-1111-1111-1111-111111111111",
    )


# ----- auth & resolution paths ------------------------------------------------


def test_unknown_app_guid_returns_404(stack):
    body = _pr_payload()
    resp = _post(
        Client(),
        "00000000-0000-0000-0000-000000000000",
        body=body,
        secret=stack["secret"],
    )
    assert resp.status_code == 404


def test_no_secret_configured_returns_401(stack):
    stack["conn"].webhook_secret_backend_kind = ""
    stack["conn"].webhook_secret_ciphertext = b""
    stack["conn"].save(
        update_fields=[
            "webhook_secret_backend_kind",
            "webhook_secret_ciphertext",
        ]
    )
    body = _pr_payload()
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"])
    assert resp.status_code == 401
    assert "secret not configured" in resp.json()["detail"]


def test_no_source_connection_returns_401(stack):
    # Deactivate the only matching connection so _pick_source_connection
    # returns None — the receiver should answer 401 just like the
    # no-secret path (same observable from outside).
    stack["conn"].is_active = False
    stack["conn"].save(update_fields=["is_active"])
    body = _pr_payload()
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"])
    assert resp.status_code == 401


def test_bad_signature_returns_401(stack):
    body = _pr_payload()
    resp = Client().post(
        f"/api/webhooks/github/{stack['app'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256="sha256=" + "0" * 64,
        HTTP_X_GITHUB_EVENT="pull_request",
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid signature"


def test_missing_signature_returns_401(stack):
    body = _pr_payload()
    resp = Client().post(
        f"/api/webhooks/github/{stack['app'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_GITHUB_EVENT="pull_request",
    )
    assert resp.status_code == 401


# ----- event filtering --------------------------------------------------------


def test_non_pull_request_event_acked(stack):
    body = json.dumps({"zen": "Anything added dilutes everything else."}).encode("utf-8")
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"], event="ping")
    assert resp.status_code == 200
    assert resp.json()["detail"] == "event not handled"
    assert PreviewEnvironment.objects.count() == 0


def test_push_event_acked(stack):
    # A push with no WorkflowWebhook bound to the app is acked with a
    # zero dispatch count (the preview lifecycle never runs for push).
    body = json.dumps({"ref": "refs/heads/main"}).encode("utf-8")
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"], event="push")
    assert resp.status_code == 200
    assert resp.json()["detail"] == "push handled"
    assert resp.json()["workflows_dispatched"] == 0
    assert PreviewEnvironment.objects.count() == 0


def test_malformed_json_returns_400(stack):
    body = b"{ not json"
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"])
    assert resp.status_code == 400
    assert "invalid JSON" in resp.json()["detail"]


def test_ignored_action_returns_200(stack):
    body = _pr_payload(action="labeled")
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"])
    assert resp.status_code == 200
    assert "not a preview trigger" in resp.json()["detail"]
    assert PreviewEnvironment.objects.count() == 0


def test_pull_request_missing_number_acked_not_500(stack):
    body = json.dumps(
        {
            "action": "opened",
            "repository": {"full_name": "acme-org/hello"},
            "pull_request": {"head": {"sha": "abc", "ref": "main"}, "user": {"type": "User"}},
        }
    ).encode("utf-8")
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"])
    assert resp.status_code == 200
    assert "missing required" in resp.json()["detail"]


# ----- BUILD_PREVIEW path -----------------------------------------------------


def test_pr_opened_creates_preview_and_returns_workflow_id(stack):
    body = _pr_payload(action="opened", pr_number=7, head_sha="cafebabe" * 5)
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"])
    assert resp.status_code == 200, resp.content

    data = resp.json()
    assert data["workflow_id"].startswith("build-preview-acme-org-hello-7-")
    assert "PR #7" in data["detail"]

    preview = PreviewEnvironment.objects.get(registered_app=stack["app"], pr_number=7)
    assert preview.status == PreviewEnvironment.Status.BUILDING
    assert preview.is_manual is False
    assert preview.namespace == "acme-pr-hello-pr-pr-7"
    assert preview.hostname == "pr-7.hello-pr.acme-pr"
    assert preview.branch == "feature/x"

    env = AppEnvironment.objects.get(registered_app=stack["app"], name="preview-pr-7")
    assert env.tenant_cluster_id == stack["cluster"].pk
    assert env.url == f"https://{preview.hostname}"
    assert preview.app_environment_id == env.pk


def test_pr_synchronize_reuses_existing_preview(stack):
    # First delivery: opened.
    first = _pr_payload(action="opened", pr_number=12, head_sha="a" * 40)
    resp1 = _post(Client(), str(stack["app"].guid), body=first, secret=stack["secret"])
    assert resp1.status_code == 200

    # Second delivery: synchronize with new SHA. The dispatcher mints
    # a new workflow_id (different SHA → different id) but the
    # PreviewEnvironment row is reused.
    second = _pr_payload(action="synchronize", pr_number=12, head_sha="b" * 40)
    resp2 = _post(Client(), str(stack["app"].guid), body=second, secret=stack["secret"])
    assert resp2.status_code == 200

    rows = PreviewEnvironment.objects.filter(registered_app=stack["app"], pr_number=12)
    assert rows.count() == 1
    assert resp1.json()["workflow_id"] != resp2.json()["workflow_id"]


def test_pr_reopened_treated_as_build(stack):
    body = _pr_payload(action="reopened", pr_number=18, head_sha="d" * 40)
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"])
    assert resp.status_code == 200
    assert resp.json()["workflow_id"].startswith("build-preview-acme-org-hello-18-")
    assert PreviewEnvironment.objects.filter(registered_app=stack["app"], pr_number=18).exists()


def test_preview_disabled_app_ignores_event(stack):
    stack["app"].preview_enabled = False
    stack["app"].save(update_fields=["preview_enabled"])

    body = _pr_payload(action="opened", pr_number=33, head_sha="e" * 40)
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"])
    assert resp.status_code == 200
    assert "preview environments enabled" in resp.json()["detail"]
    assert PreviewEnvironment.objects.count() == 0


def test_no_default_tenant_cluster_returns_200_with_reason(stack):
    stack["app"].default_tenant_cluster = None
    stack["app"].save(update_fields=["default_tenant_cluster"])

    body = _pr_payload(action="opened", pr_number=44, head_sha="f" * 40)
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"])
    assert resp.status_code == 200
    assert "default tenant cluster" in resp.json()["detail"]
    assert PreviewEnvironment.objects.count() == 0


# ----- TEARDOWN_PREVIEW path --------------------------------------------------


def test_pr_closed_without_existing_preview_acked(stack):
    body = _pr_payload(action="closed", pr_number=99, head_sha="9" * 40, merged=True)
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"])
    assert resp.status_code == 200
    assert resp.json()["detail"] == "no preview to tear down"


def test_pr_closed_with_preview_starts_teardown(stack):
    # Land a build first so there's something to tear down.
    build_body = _pr_payload(action="opened", pr_number=55, head_sha="5" * 40)
    build_resp = _post(Client(), str(stack["app"].guid), body=build_body, secret=stack["secret"])
    assert build_resp.status_code == 200

    close_body = _pr_payload(action="closed", pr_number=55, head_sha="5" * 40, merged=True)
    close_resp = _post(Client(), str(stack["app"].guid), body=close_body, secret=stack["secret"])
    assert close_resp.status_code == 200

    data = close_resp.json()
    assert data["workflow_id"] == "teardown-preview-acme-org-hello-55"
    assert "merged" in data["detail"]


def test_pr_closed_unmerged_starts_teardown(stack):
    build_body = _pr_payload(action="opened", pr_number=56, head_sha="6" * 40)
    _post(Client(), str(stack["app"].guid), body=build_body, secret=stack["secret"])

    close_body = _pr_payload(action="closed", pr_number=56, head_sha="6" * 40, merged=False)
    resp = _post(Client(), str(stack["app"].guid), body=close_body, secret=stack["secret"])
    assert resp.status_code == 200
    assert resp.json()["workflow_id"] == "teardown-preview-acme-org-hello-56"
    assert "closed" in resp.json()["detail"]
