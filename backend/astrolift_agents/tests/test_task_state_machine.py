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


# ---------------------------------------------------------------------------
# DispatcherInstance defaults
# ---------------------------------------------------------------------------


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
