"""SCM push / PR webhook → WorkflowWebhook trigger bridge.

Covers ``_dispatch_workflow_webhooks`` as exercised through the live
``POST /api/webhooks/github/<app_guid>/`` receiver:

* A signed ``push`` delivery to an app with an enabled WorkflowWebhook
  launches a WorkflowInstance (real DB row, not a mock) and the ack
  reports the dispatch count.
* A signed ``pull_request`` delivery fires the same bridge *in addition*
  to the preview-environment lifecycle.
* Disabled webhooks, and webhooks bound to a different app, are NOT
  fired — the query is scoped to ``registered_app`` + ``enabled``.
* A WorkflowWebhook whose dispatch raises is swallowed: SCM ingest still
  answers 200, and a sibling webhook on the same app still fires.

Temporal is disabled (``ASTROLIFT_TEMPORAL_ENABLED=False``) so the
inner ``trigger_workflow_instance`` writes the WorkflowInstance row but
skips the Temporal enqueue — the assertions cover the row state that
lands on the DB.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from django.test import Client

from astrolift_agents.models import WorkflowWebhook
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import PreviewEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from core.secrets import encrypt_at_rest
from workflows.models import WorkflowDefinition, WorkflowInstance

pytestmark = pytest.mark.django_db


_STATES = [
    {"name": "pending", "label": "Pending", "is_initial": True, "is_final": False},
    {"name": "done", "label": "Done", "is_initial": False, "is_final": True},
]


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
    org = Organization.objects.create(name="Acme", slug="acme-wf")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-wf")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-wf")
    plugin = ProviderPlugin(
        name="P",
        slug="p-wf",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    cluster = TenantCluster.objects.create(
        organization=org,
        name="c",
        slug="c-wf",
        provider_plugin=ProviderPlugin.objects.get(slug="p-wf"),
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
        slug="hello-wf",
        provisioning_status="ready",
        source_kind="github",
        source_repo="acme-org/hello",
        deploy_branch="main",
        trigger_mode=RegisteredApp.TriggerMode.AUTO_ON_PUSH.value,
        preview_enabled=True,
        default_tenant_cluster=cluster,
    )

    secret = "whsec_wf_value_789"
    encrypted = encrypt_at_rest(secret.encode("utf-8"))
    SourceConnection.objects.create(
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
    return {"org": org, "app": app, "secret": secret, "cluster": cluster}


def _definition(slug: str) -> WorkflowDefinition:
    return WorkflowDefinition.objects.create(
        name=f"Def {slug}",
        slug=slug,
        model_label="workflows.workflowdefinition",
        states=_STATES,
        transitions=[{"from_state": "pending", "to_state": "done", "label": "Complete"}],
        is_enabled=True,
    )


def _webhook(app, definition, *, slug: str, enabled: bool = True) -> WorkflowWebhook:
    return WorkflowWebhook.objects.create(
        workflow_definition=definition,
        registered_app=app,
        slug=slug,
        secret_hash=hashlib.sha256(b"x").hexdigest(),
        input_mapping={},
        enabled=enabled,
    )


def _github_sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _push_payload(*, ref: str = "refs/heads/main", repo: str = "acme-org/hello") -> bytes:
    return json.dumps(
        {
            "ref": ref,
            "repository": {"full_name": repo},
            "after": "f" * 40,
            "commits": [{"id": "f" * 40, "message": "ship it"}],
        }
    ).encode("utf-8")


def _pr_payload(*, action: str = "opened", pr_number: int = 7) -> bytes:
    return json.dumps(
        {
            "action": action,
            "repository": {"full_name": "acme-org/hello"},
            "pull_request": {
                "number": pr_number,
                "merged": False,
                "head": {"sha": "a" * 40, "ref": "feature/x"},
                "user": {"type": "User"},
            },
        }
    ).encode("utf-8")


def _post(client, app_guid, *, body, secret, event):
    return client.post(
        f"/api/webhooks/github/{app_guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_github_sig(secret, body),
        HTTP_X_GITHUB_EVENT=event,
        HTTP_X_GITHUB_DELIVERY="22222222-2222-2222-2222-222222222222",
    )


# ----- allowed paths ----------------------------------------------------------


def test_push_with_enabled_webhook_launches_instance(stack):
    definition = _definition("deploy-on-push")
    _webhook(stack["app"], definition, slug="hook-push")

    body = _push_payload()
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"], event="push")

    assert resp.status_code == 200, resp.content
    data = resp.json()
    assert data["detail"] == "push handled"
    assert data["workflows_dispatched"] == 1

    # A real WorkflowInstance row was created against the bound definition.
    instances = WorkflowInstance.objects.filter(workflow=definition)
    assert instances.count() == 1
    assert instances.first().current_state == "pending"


def test_push_dispatch_passes_payload_and_trigger_kind(stack, monkeypatch):
    definition = _definition("spy-def")
    _webhook(stack["app"], definition, slug="hook-spy")

    calls = []

    def _spy(defn, payload, *, trigger_kind="manual", **kw):
        calls.append((defn, payload, trigger_kind))

    monkeypatch.setattr(
        "astrolift_agents.services.workflow_triggers.trigger_workflow_instance",
        _spy,
    )

    body = _push_payload(ref="refs/heads/release")
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"], event="push")
    assert resp.status_code == 200

    assert len(calls) == 1
    defn, payload, trigger_kind = calls[0]
    assert defn.pk == definition.pk
    # Whole payload is handed through; the workflow definition projects it.
    assert payload["ref"] == "refs/heads/release"
    assert payload["repository"]["full_name"] == "acme-org/hello"
    assert trigger_kind == "scm_push"


def test_pull_request_fires_webhook_and_preview(stack):
    definition = _definition("on-pr")
    _webhook(stack["app"], definition, slug="hook-pr")

    body = _pr_payload(action="opened", pr_number=21)
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"], event="pull_request")
    assert resp.status_code == 200, resp.content
    # Preview lifecycle still ran (existing contract preserved).
    assert "workflow_id" in resp.json()
    assert PreviewEnvironment.objects.filter(registered_app=stack["app"], pr_number=21).exists()
    # AND the workflow webhook fired.
    assert WorkflowInstance.objects.filter(workflow=definition).count() == 1


def test_multiple_enabled_webhooks_all_fire(stack):
    d1 = _definition("def-a")
    d2 = _definition("def-b")
    _webhook(stack["app"], d1, slug="hook-a")
    _webhook(stack["app"], d2, slug="hook-b")

    body = _push_payload()
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"], event="push")
    assert resp.status_code == 200
    assert resp.json()["workflows_dispatched"] == 2
    assert WorkflowInstance.objects.filter(workflow=d1).count() == 1
    assert WorkflowInstance.objects.filter(workflow=d2).count() == 1


# ----- denied / scoping paths -------------------------------------------------


def test_disabled_webhook_does_not_fire(stack):
    definition = _definition("disabled-def")
    _webhook(stack["app"], definition, slug="hook-off", enabled=False)

    body = _push_payload()
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"], event="push")
    assert resp.status_code == 200
    assert resp.json()["workflows_dispatched"] == 0
    assert WorkflowInstance.objects.filter(workflow=definition).count() == 0


def test_webhook_bound_to_other_app_does_not_fire(stack):
    # A webhook scoped to a *different* app must not fire for this app's
    # push — the dispatch query is registered_app-scoped.
    other_app = RegisteredApp.objects.create(
        organization=stack["org"],
        project=None,
        team=stack["app"].team,
        name="Other",
        slug="other-wf",
        provisioning_status="ready",
        source_kind="github",
        source_repo="acme-org/other",
        deploy_branch="main",
        trigger_mode=RegisteredApp.TriggerMode.AUTO_ON_PUSH.value,
    )
    definition = _definition("other-def")
    _webhook(other_app, definition, slug="hook-other")

    body = _push_payload()
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"], event="push")
    assert resp.status_code == 200
    assert resp.json()["workflows_dispatched"] == 0
    assert WorkflowInstance.objects.filter(workflow=definition).count() == 0


def test_unbound_org_level_webhook_does_not_fire(stack):
    # An org-level webhook (registered_app is NULL) is resolved by slug on
    # its own endpoint, never by the SCM ingest path. It must not fire on
    # an app push even though it is enabled.
    definition = _definition("org-level-def")
    WorkflowWebhook.objects.create(
        workflow_definition=definition,
        registered_app=None,
        slug="hook-org-level",
        secret_hash=hashlib.sha256(b"y").hexdigest(),
        input_mapping={},
        enabled=True,
    )

    body = _push_payload()
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"], event="push")
    assert resp.status_code == 200
    assert resp.json()["workflows_dispatched"] == 0
    assert WorkflowInstance.objects.filter(workflow=definition).count() == 0


# ----- failure isolation ------------------------------------------------------


def test_one_failing_webhook_does_not_break_ingest_or_siblings(stack, monkeypatch):
    good = _definition("good-def")
    bad = _definition("bad-def")
    _webhook(stack["app"], bad, slug="hook-bad")
    _webhook(stack["app"], good, slug="hook-good")

    real_start = WorkflowInstance.start.__func__

    def _flaky_start(cls, workflow, obj, user=None):
        if workflow.pk == bad.pk:
            raise RuntimeError("boom")
        return real_start(cls, workflow, obj, user=user)

    monkeypatch.setattr(WorkflowInstance, "start", classmethod(_flaky_start))

    body = _push_payload()
    resp = _post(Client(), str(stack["app"].guid), body=body, secret=stack["secret"], event="push")

    # Ingest still acks 200; the bad webhook is swallowed.
    assert resp.status_code == 200
    # Only the good one counts as dispatched.
    assert resp.json()["workflows_dispatched"] == 1
    assert WorkflowInstance.objects.filter(workflow=good).count() == 1
    assert WorkflowInstance.objects.filter(workflow=bad).count() == 0


def test_bad_signature_never_reaches_dispatch(stack):
    definition = _definition("guarded-def")
    _webhook(stack["app"], definition, slug="hook-guarded")

    body = _push_payload()
    resp = Client().post(
        f"/api/webhooks/github/{stack['app'].guid}/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256="sha256=" + "0" * 64,
        HTTP_X_GITHUB_EVENT="push",
    )
    assert resp.status_code == 401
    # The trust boundary fired before any workflow dispatch.
    assert WorkflowInstance.objects.filter(workflow=definition).count() == 0
