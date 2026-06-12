"""Tests for the thread-mode agent checkin + callback endpoints.

Runs against a real Postgres (pytest-django). No mocks. Exercises both
the authorized path (valid dispatcher Bearer token) and the denied paths
(bad token, foreign-org task, non-dispatchable state).

These endpoints back ``runner.py`` in the agent Docker images:

    POST /api/dispatch/v1/agents/<task_id>/checkin/
    POST /api/dispatch/v1/agents/<task_id>/callback/
"""

from __future__ import annotations

import hashlib
import json

import pytest
from django.test import Client

from astrolift_agents.models import AgentTask, Brief, DispatcherInstance
from astrolift_identity.models import Organization

CHECKIN = "/api/dispatch/v1/agents/{}/checkin/"
CALLBACK = "/api/dispatch/v1/agents/{}/callback/"


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
    return Organization.objects.create(name="CheckinOrg", slug="checkin-org")


@pytest.fixture
def raw_key() -> str:
    return "k" * 64


@pytest.fixture
def dispatcher(org, raw_key):
    return DispatcherInstance.objects.create(
        organization=org,
        name="Dispatcher A",
        slug="checkin-dispatcher-a",
        endpoint="https://dispatch.example.com/",
        api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        cloud=DispatcherInstance.Cloud.K8S_NATIVE,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
    )


@pytest.fixture
def brief(org):
    return Brief.objects.create(
        organization=org,
        content_hash="d" * 64,
        manifest_snapshot={
            "system_prompt": "Implement the feature and run the tests.",
            "tools": ["bash", "read", "edit"],
            "model": "claude-opus-4",
        },
        context={"project": "demo", "actor": "user-123"},
    )


def _auth(raw_key: str) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


def _make_task(org, dispatcher, brief, status=AgentTask.Status.QUEUED):
    task = AgentTask.objects.create(
        organization=org,
        dispatcher=dispatcher,
        brief=brief,
        callback_url="https://controller.example.com/cb",
    )
    # Status defaults to DRAFT; set the desired starting state directly so
    # we don't have to walk the whole state machine in test setup.
    if status != AgentTask.Status.DRAFT:
        AgentTask.all_objects.filter(pk=task.pk).update(status=status)
        task.refresh_from_db()
    return task


# ---------------------------------------------------------------------------
# checkin
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_checkin_returns_brief_packet(org, dispatcher, brief, raw_key):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.QUEUED)
    resp = Client().post(CHECKIN.format(task.guid), **_auth(raw_key))

    assert resp.status_code == 200, resp.content
    payload = resp.json()
    assert payload["task_id"] == str(task.guid)
    assert payload["prompt"] == "Implement the feature and run the tests."
    assert payload["system"] == "Implement the feature and run the tests."
    assert payload["tools"] == ["bash", "read", "edit"]
    assert payload["context"] == {"project": "demo", "actor": "user-123"}
    assert payload["model"] == "claude-opus-4"
    assert payload["callback_url"] == "https://controller.example.com/cb"


@pytest.mark.django_db(transaction=True)
def test_checkin_advances_queued_task_to_running(org, dispatcher, brief, raw_key):
    """A QUEUED task must walk QUEUED -> PROVISIONING -> RUNNING.

    Regression: a direct transition_to(RUNNING) from QUEUED is illegal in
    the state graph and silently no-ops, leaving the task stuck.
    """
    task = _make_task(org, dispatcher, brief, AgentTask.Status.QUEUED)
    resp = Client().post(CHECKIN.format(task.guid), **_auth(raw_key))

    assert resp.status_code == 200
    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING
    assert task.provisioning_at is not None
    assert task.started_at is not None


@pytest.mark.django_db(transaction=True)
def test_checkin_idempotent_when_already_running(org, dispatcher, brief, raw_key):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    resp = Client().post(CHECKIN.format(task.guid), **_auth(raw_key))

    assert resp.status_code == 200
    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING


@pytest.mark.django_db(transaction=True)
def test_checkin_missing_brief_falls_back_to_default_system(org, dispatcher, raw_key):
    task = _make_task(org, dispatcher, brief=None, status=AgentTask.Status.QUEUED)
    resp = Client().post(CHECKIN.format(task.guid), **_auth(raw_key))

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["prompt"] == ""
    assert payload["system"].startswith("You are a helpful agent")
    assert payload["tools"] == []
    assert payload["context"] == {}


@pytest.mark.django_db(transaction=True)
def test_checkin_rejects_non_dispatchable_state(org, dispatcher, brief, raw_key):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.DRAFT)
    resp = Client().post(CHECKIN.format(task.guid), **_auth(raw_key))

    assert resp.status_code == 409
    assert "dispatchable" in resp.json()["error"]
    task.refresh_from_db()
    assert task.status == AgentTask.Status.DRAFT  # unchanged


@pytest.mark.django_db(transaction=True)
def test_checkin_unknown_task_404(org, dispatcher, raw_key):
    resp = Client().post(
        CHECKIN.format("00000000-0000-0000-0000-000000000000"),
        **_auth(raw_key),
    )
    assert resp.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_checkin_foreign_org_task_404(dispatcher, brief, raw_key):
    """A dispatcher must not see tasks belonging to another org."""
    other_org = Organization.objects.create(name="OtherOrg", slug="other-org")
    other_brief = Brief.objects.create(organization=other_org, content_hash="e" * 64)
    task = AgentTask.objects.create(organization=other_org, brief=other_brief)
    AgentTask.all_objects.filter(pk=task.pk).update(status=AgentTask.Status.QUEUED)

    resp = Client().post(CHECKIN.format(task.guid), **_auth(raw_key))
    assert resp.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_checkin_bad_token_401(org, dispatcher, brief):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.QUEUED)
    resp = Client().post(CHECKIN.format(task.guid), **_auth("wrong-key"))
    assert resp.status_code == 401


@pytest.mark.django_db(transaction=True)
def test_checkin_no_auth_header_401(org, dispatcher, brief):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.QUEUED)
    resp = Client().post(CHECKIN.format(task.guid))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# callback
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_callback_completed_persists_result(org, dispatcher, brief, raw_key):
    """Completion must persist the result AND transition to COMPLETED.

    Regression: transition_to() saves a fixed update_fields set that omits
    ``result``, so the output would be dropped if not saved explicitly.
    """
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    resp = Client().post(
        CALLBACK.format(task.guid),
        data=json.dumps({"status": "completed", "result": "all green"}),
        content_type="application/json",
        **_auth(raw_key),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["continue"] is False

    task.refresh_from_db()
    assert task.status == AgentTask.Status.COMPLETED
    assert task.result == {"output": "all green"}
    assert task.ended_at is not None


@pytest.mark.django_db(transaction=True)
def test_callback_failed_persists_failure(org, dispatcher, brief, raw_key):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    resp = Client().post(
        CALLBACK.format(task.guid),
        data=json.dumps({"status": "failed", "error": "boom", "result": "partial work"}),
        content_type="application/json",
        **_auth(raw_key),
    )

    assert resp.status_code == 200
    assert resp.json()["continue"] is False

    task.refresh_from_db()
    assert task.status == AgentTask.Status.FAILED
    assert task.failure == {"message": "boom", "output": "partial work"}


@pytest.mark.django_db(transaction=True)
def test_callback_heartbeat_keeps_running(org, dispatcher, brief, raw_key):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    resp = Client().post(
        CALLBACK.format(task.guid),
        data=json.dumps({"status": "running", "partial": "thinking..."}),
        content_type="application/json",
        **_auth(raw_key),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["continue"] is True  # still RUNNING -> agent keeps going

    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING


@pytest.mark.django_db(transaction=True)
def test_callback_already_terminal_keeps_output(org, dispatcher, brief, raw_key):
    """A late callback on a terminal task records output without crashing."""
    task = _make_task(org, dispatcher, brief, AgentTask.Status.COMPLETED)
    resp = Client().post(
        CALLBACK.format(task.guid),
        data=json.dumps({"status": "completed", "result": "late report"}),
        content_type="application/json",
        **_auth(raw_key),
    )

    assert resp.status_code == 200
    assert resp.json()["continue"] is False
    task.refresh_from_db()
    assert task.status == AgentTask.Status.COMPLETED
    assert task.result == {"output": "late report"}


@pytest.mark.django_db(transaction=True)
def test_callback_invalid_json_400(org, dispatcher, brief, raw_key):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    resp = Client().post(
        CALLBACK.format(task.guid),
        data="not json",
        content_type="application/json",
        **_auth(raw_key),
    )
    assert resp.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_callback_unknown_task_404(org, dispatcher, raw_key):
    resp = Client().post(
        CALLBACK.format("00000000-0000-0000-0000-000000000000"),
        data=json.dumps({"status": "running"}),
        content_type="application/json",
        **_auth(raw_key),
    )
    assert resp.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_callback_bad_token_401(org, dispatcher, brief):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    resp = Client().post(
        CALLBACK.format(task.guid),
        data=json.dumps({"status": "running"}),
        content_type="application/json",
        **_auth("nope"),
    )
    assert resp.status_code == 401
