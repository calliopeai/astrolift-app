"""Tests for the dispatcher push-mode task status callback (#877).

Runs against a real Postgres (pytest-django). No mocks. Covers the
correctness fix where a vnc-enabled task reaching RUNNING via this
push-mode callback must have its ``vnc_url`` published — otherwise the
noVNC viewer has no relay path to connect to.

    POST /api/dispatch/v1/tasks/<task_id>/status/
"""

from __future__ import annotations

import hashlib
import json

import pytest
from django.test import Client

from astrolift_agents.models import AgentTask, DispatcherInstance
from astrolift_identity.models import Organization

STATUS = "/api/dispatch/v1/tasks/{}/status/"


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Silence the User -> Profile -> OpenSearch indexing chain."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="StatusOrg", slug="status-org")


@pytest.fixture
def raw_key() -> str:
    return "s" * 64


@pytest.fixture
def dispatcher(org, raw_key):
    return DispatcherInstance.objects.create(
        organization=org,
        name="Status Dispatcher",
        slug="status-dispatcher-a",
        endpoint="https://dispatch.example.com/",
        api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        cloud=DispatcherInstance.Cloud.K8S_NATIVE,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
    )


def _auth(raw_key: str) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


def _make_task(org, dispatcher, *, vnc_enabled: bool, status=AgentTask.Status.PROVISIONING):
    task = AgentTask.objects.create(
        organization=org,
        dispatcher=dispatcher,
        vnc_enabled=vnc_enabled,
    )
    # Set the desired starting state directly to avoid walking the whole
    # state machine in setup (mirrors test_agent_checkin._make_task).
    if status != AgentTask.Status.DRAFT:
        AgentTask.all_objects.filter(pk=task.pk).update(status=status)
        task.refresh_from_db()
    return task


@pytest.mark.django_db(transaction=True)
def test_running_publishes_vnc_url_for_vnc_task(org, dispatcher, raw_key):
    """The push-mode RUNNING callback must publish vnc_url for a
    vnc-enabled task so the live viewer has a relay path.

    Regression: only the Temporal transition_to() path used to set
    vnc_url, so a task driven RUNNING here kept an empty vnc_url forever.
    """
    task = _make_task(org, dispatcher, vnc_enabled=True)
    assert task.vnc_url == ""  # nothing published yet

    resp = Client().post(
        STATUS.format(task.guid),
        data=json.dumps({"status": "running"}),
        content_type="application/json",
        **_auth(raw_key),
    )

    assert resp.status_code == 200, resp.content
    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING
    assert task.started_at is not None
    # The relay path matches the ASGI handler registered at
    # /app/vnc/<guid> (core.schema.vnc_ws) and the model's own format.
    assert task.vnc_url == f"/app/vnc/{task.guid}"


@pytest.mark.django_db(transaction=True)
def test_running_leaves_vnc_url_empty_for_non_vnc_task(org, dispatcher, raw_key):
    """A task that is NOT vnc-enabled must not get a vnc_url — the relay
    would refuse it, and a stray URL would falsely advertise a viewer."""
    task = _make_task(org, dispatcher, vnc_enabled=False)

    resp = Client().post(
        STATUS.format(task.guid),
        data=json.dumps({"status": "running"}),
        content_type="application/json",
        **_auth(raw_key),
    )

    assert resp.status_code == 200, resp.content
    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING
    assert task.vnc_url == ""


@pytest.mark.django_db(transaction=True)
def test_running_preserves_existing_vnc_url(org, dispatcher, raw_key):
    """If vnc_url was already published (e.g. the Temporal path beat the
    callback), the callback must not overwrite it."""
    task = _make_task(org, dispatcher, vnc_enabled=True)
    AgentTask.all_objects.filter(pk=task.pk).update(vnc_url="/app/vnc/preexisting")
    task.refresh_from_db()

    resp = Client().post(
        STATUS.format(task.guid),
        data=json.dumps({"status": "running"}),
        content_type="application/json",
        **_auth(raw_key),
    )

    assert resp.status_code == 200, resp.content
    task.refresh_from_db()
    assert task.vnc_url == "/app/vnc/preexisting"


@pytest.mark.django_db(transaction=True)
def test_terminal_status_does_not_publish_vnc_url(org, dispatcher, raw_key):
    """A non-RUNNING transition (e.g. completed) must not publish a
    vnc_url even for a vnc-enabled task — there is no live framebuffer."""
    task = _make_task(org, dispatcher, vnc_enabled=True, status=AgentTask.Status.RUNNING)

    resp = Client().post(
        STATUS.format(task.guid),
        data=json.dumps({"status": "completed"}),
        content_type="application/json",
        **_auth(raw_key),
    )

    assert resp.status_code == 200, resp.content
    task.refresh_from_db()
    assert task.status == AgentTask.Status.COMPLETED
    assert task.vnc_url == ""
    assert task.ended_at is not None


@pytest.mark.django_db(transaction=True)
def test_running_callback_cannot_resurrect_cancelled_task(org, dispatcher, raw_key):
    task = _make_task(org, dispatcher, vnc_enabled=False)
    task.transition_to(AgentTask.Status.CANCELLED)

    resp = Client().post(
        STATUS.format(task.guid),
        data=json.dumps({"status": "running"}),
        content_type="application/json",
        **_auth(raw_key),
    )

    assert resp.status_code == 409
    task.refresh_from_db()
    assert task.status == AgentTask.Status.CANCELLED


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", [STATUS, "/api/dispatch/v1/agents/{}/callback/"])
def test_pending_operator_stop_rejects_late_producer_failure(org, dispatcher, raw_key, endpoint):
    from django.utils import timezone

    task = _make_task(org, dispatcher, vnc_enabled=False, status=AgentTask.Status.RUNNING)
    task.cancel_requested_at = timezone.now()
    task.save(update_fields=["cancel_requested_at"])
    response = Client().post(
        endpoint.format(task.guid),
        data=json.dumps({"status": "failed", "error": "killed", "error_message": "killed"}),
        content_type="application/json",
        **_auth(raw_key),
    )
    assert response.status_code == 409
    assert response.json()["continue"] is False
    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING
    assert task.failure is None
