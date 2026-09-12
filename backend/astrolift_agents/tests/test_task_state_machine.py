"""
Tests for AgentTask state machine + DispatcherInstance defaults.

State machine rules (issue #44):
  DRAFT → QUEUED (valid)
  QUEUED → RUNNING (invalid — must pass through PROVISIONING)
  RUNNING → COMPLETED (valid)
  COMPLETED → CANCELLED (invalid — terminal state)

DispatcherInstance.heartbeat_ttl_seconds defaults to 30.
"""

from __future__ import annotations

import pytest

from astrolift_agents.models import AgentTask, DispatcherInstance
from astrolift_identity.models import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    """Silence User → Profile → OpenSearch indexing in test helpers."""
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
    return Organization.objects.create(name="State Machine Org", slug="sm-org-test")


@pytest.fixture
def draft_task(org):
    return AgentTask.objects.create(organization=org)


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------


def test_draft_to_queued_is_valid(draft_task):
    draft_task.transition_to(AgentTask.Status.QUEUED)
    draft_task.refresh_from_db()
    assert draft_task.status == AgentTask.Status.QUEUED
    assert draft_task.queued_at is not None


def test_queued_to_provisioning_is_valid(draft_task):
    draft_task.transition_to(AgentTask.Status.QUEUED)
    draft_task.transition_to(AgentTask.Status.PROVISIONING)
    draft_task.refresh_from_db()
    assert draft_task.status == AgentTask.Status.PROVISIONING
    assert draft_task.provisioning_at is not None


def test_draft_preparation_failure_can_settle(draft_task):
    draft_task.failure = {"message": "package assembly failed"}
    draft_task.save(update_fields=["failure", "updated_at", "version"])
    draft_task.transition_to(AgentTask.Status.FAILED)
    draft_task.refresh_from_db()
    assert draft_task.status == AgentTask.Status.FAILED
    assert draft_task.ended_at is not None


def test_queued_workflow_start_failure_can_settle(draft_task):
    draft_task.transition_to(AgentTask.Status.QUEUED)
    draft_task.transition_to(AgentTask.Status.FAILED)
    draft_task.refresh_from_db()
    assert draft_task.status == AgentTask.Status.FAILED


def test_running_to_completed_is_valid(draft_task):
    draft_task.transition_to(AgentTask.Status.QUEUED)
    draft_task.transition_to(AgentTask.Status.PROVISIONING)
    draft_task.transition_to(AgentTask.Status.RUNNING)
    draft_task.transition_to(AgentTask.Status.COMPLETED)
    draft_task.refresh_from_db()
    assert draft_task.status == AgentTask.Status.COMPLETED
    assert draft_task.ended_at is not None


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


def test_queued_to_running_directly_is_invalid(draft_task):
    """QUEUED → RUNNING must go through PROVISIONING."""
    draft_task.transition_to(AgentTask.Status.QUEUED)
    with pytest.raises(ValueError, match="cannot transition"):
        draft_task.transition_to(AgentTask.Status.RUNNING)


def test_cancel_completed_task_is_invalid(draft_task):
    """COMPLETED is terminal — cannot transition to CANCELLED."""
    draft_task.transition_to(AgentTask.Status.QUEUED)
    draft_task.transition_to(AgentTask.Status.PROVISIONING)
    draft_task.transition_to(AgentTask.Status.RUNNING)
    draft_task.transition_to(AgentTask.Status.COMPLETED)
    with pytest.raises(ValueError, match="cannot transition"):
        draft_task.transition_to(AgentTask.Status.CANCELLED)


def test_cancel_from_provisioning_is_valid(draft_task):
    """PROVISIONING → CANCELLED is allowed (before container starts)."""
    draft_task.transition_to(AgentTask.Status.QUEUED)
    draft_task.transition_to(AgentTask.Status.PROVISIONING)
    draft_task.transition_to(AgentTask.Status.CANCELLED)
    draft_task.refresh_from_db()
    assert draft_task.status == AgentTask.Status.CANCELLED


def test_stale_poll_cannot_resurrect_cancelled_task(draft_task):
    draft_task.transition_to(AgentTask.Status.QUEUED)
    draft_task.transition_to(AgentTask.Status.PROVISIONING)
    stale_poll = AgentTask.all_objects.get(pk=draft_task.pk)

    draft_task.transition_to(AgentTask.Status.CANCELLED)

    with pytest.raises(ValueError, match="cannot transition"):
        stale_poll.transition_to(AgentTask.Status.RUNNING)
    stale_poll.refresh_from_db()
    assert stale_poll.status == AgentTask.Status.CANCELLED


# ---------------------------------------------------------------------------
# DispatcherInstance defaults
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# VNC url population (#877)
# ---------------------------------------------------------------------------


def test_vnc_url_set_on_running_when_vnc_enabled(org):
    """A VNC-capable task publishes the relay path when it reaches RUNNING."""
    task = AgentTask.objects.create(organization=org, vnc_enabled=True)
    task.transition_to(AgentTask.Status.QUEUED)
    task.transition_to(AgentTask.Status.PROVISIONING)
    assert task.vnc_url == ""  # not RUNNING yet — no framebuffer

    task.transition_to(AgentTask.Status.RUNNING)
    task.refresh_from_db()
    assert task.vnc_url == f"/app/vnc/{task.guid}"


def test_vnc_url_empty_when_vnc_disabled(org):
    """A non-VNC task never publishes a relay path."""
    task = AgentTask.objects.create(organization=org, vnc_enabled=False)
    task.transition_to(AgentTask.Status.QUEUED)
    task.transition_to(AgentTask.Status.PROVISIONING)
    task.transition_to(AgentTask.Status.RUNNING)
    task.refresh_from_db()
    assert task.vnc_url == ""


def test_vnc_url_not_set_before_running(org):
    """Provisioning a VNC task does not yet expose a framebuffer path."""
    task = AgentTask.objects.create(organization=org, vnc_enabled=True)
    task.transition_to(AgentTask.Status.QUEUED)
    task.refresh_from_db()
    assert task.vnc_url == ""


def test_graphql_type_exposes_vnc_fields_when_running(org):
    """The GraphQL mapper surfaces vnc_enabled + vnc_url to the frontend."""
    from astrolift_agents.schema.types import agent_task_to_type

    task = AgentTask.objects.create(organization=org, vnc_enabled=True)
    task.transition_to(AgentTask.Status.QUEUED)
    task.transition_to(AgentTask.Status.PROVISIONING)
    task.transition_to(AgentTask.Status.RUNNING)

    gql = agent_task_to_type(task, can_watch=True)
    assert gql.vnc_enabled is True
    assert gql.vnc_url == f"/app/vnc/{task.guid}"


def test_graphql_type_vnc_fields_empty_when_disabled(org):
    """A non-VNC task maps to vnc_enabled False and an empty url."""
    from astrolift_agents.schema.types import agent_task_to_type

    task = AgentTask.objects.create(organization=org, vnc_enabled=False)
    task.transition_to(AgentTask.Status.QUEUED)
    task.transition_to(AgentTask.Status.PROVISIONING)
    task.transition_to(AgentTask.Status.RUNNING)

    gql = agent_task_to_type(task)
    assert gql.vnc_enabled is False
    assert gql.vnc_url == ""


def test_dispatcher_heartbeat_ttl_default(org):
    dispatcher = DispatcherInstance.objects.create(
        organization=org,
        name="Test Dispatcher",
        slug="test-dispatcher-sm",
        endpoint="https://dispatch.example.com",
        cloud=DispatcherInstance.Cloud.AWS,
        backend=DispatcherInstance.Backend.K8S_JOB,
    )
    assert dispatcher.heartbeat_ttl_seconds == 30
    assert dispatcher.status == DispatcherInstance.Status.PENDING
    assert dispatcher.last_heartbeat_at is None
