"""Agent trigger webhook creation + inbound endpoint (#983).

Covers the seam that was missing: creating an agent-bound WorkflowWebhook and
firing it over HTTP. Dispatch itself (dispatch_agent_task_from_webhook) is
covered elsewhere and stubbed here so these isolate the new creation + the
endpoint's signature check + routing.
"""

from __future__ import annotations

import hashlib

import pytest
from django.test import Client

from astrolift_agents.models import WorkflowWebhook
from astrolift_agents.services.workflow_triggers import create_agent_webhook_trigger
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload

pytestmark = pytest.mark.django_db


@pytest.fixture
def agent_workload():
    org = Organization.objects.create(name="Acme", slug="acme-trig")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-trig")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-trig")
    app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Hello",
        slug="hello-trig",
        provisioning_status="ready",
        source_kind="direct_upload",
    )
    workload = Workload.objects.create(
        registered_app=app,
        name="Agent",
        slug="hello-trig-agent",
        kind=Workload.Kind.AGENT,
    )
    return workload


def test_create_agent_webhook_trigger_persists_and_hashes_secret(agent_workload):
    result = create_agent_webhook_trigger(agent_workload)

    assert result["endpoint"] == f"/api/webhooks/workflow/acme-trig/{result['slug']}"
    hook = WorkflowWebhook.objects.get(slug=result["slug"])
    assert hook.agent_definition_id == agent_workload.pk
    assert hook.organization_id == agent_workload.registered_app.organization_id
    assert hook.workflow_definition_id is None
    assert hook.enabled is True
    # Stored as a hash of the returned plaintext, never the plaintext itself.
    assert hook.secret_hash == hashlib.sha256(result["signing_secret"].encode()).hexdigest()
    assert result["signing_secret"] not in (hook.secret_hash, "")


def test_webhook_endpoint_dispatches_on_valid_signature(agent_workload, monkeypatch):
    result = create_agent_webhook_trigger(agent_workload)

    class _Task:
        guid = "abcdef01-0000-0000-0000-000000000000"

    captured = {}

    def _fake_dispatch(webhook, payload):
        captured["slug"] = webhook.slug
        captured["payload"] = payload
        return _Task()

    monkeypatch.setattr(
        "astrolift_agents.services.workflow_triggers.dispatch_agent_task_from_webhook",
        _fake_dispatch,
    )

    resp = Client().post(
        result["endpoint"],
        data='{"ref": "x"}',
        content_type="application/json",
        HTTP_X_ASTROLIFT_SIGNATURE=result["signing_secret"],
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["ok"] is True and body["dispatched"] is True
    assert body["taskId"] == "abcdef01-0000-0000-0000-000000000000"
    assert captured["slug"] == result["slug"]
    assert captured["payload"] == {"ref": "x"}


def test_webhook_endpoint_rejects_bad_signature(agent_workload, monkeypatch):
    result = create_agent_webhook_trigger(agent_workload)

    def _boom(webhook, payload):  # pragma: no cover - must not be called
        raise AssertionError("dispatch must not run on a bad signature")

    monkeypatch.setattr(
        "astrolift_agents.services.workflow_triggers.dispatch_agent_task_from_webhook",
        _boom,
    )

    resp = Client().post(
        result["endpoint"],
        data="{}",
        content_type="application/json",
        HTTP_X_ASTROLIFT_SIGNATURE="wrong-secret",
    )
    assert resp.status_code == 401


def test_webhook_endpoint_unknown_slug_is_404(agent_workload):
    resp = Client().post(
        "/api/webhooks/workflow/acme-trig/nope-nope",
        data="{}",
        content_type="application/json",
        HTTP_X_ASTROLIFT_SIGNATURE="whatever",
    )
    assert resp.status_code == 404
