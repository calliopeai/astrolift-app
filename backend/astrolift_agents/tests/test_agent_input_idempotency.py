"""Real PostgreSQL admission and recovery after an enqueue reply is lost (#1842)."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone

from astrolift_agents.models import AgentTask, AgentTaskInputMessage
from astrolift_agents.services.agent_task_input import (
    AgentTaskInputError,
    claim_pending_input,
    queue_agent_task_input,
)
from astrolift_identity.models import Organization
from config.schema import schema
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context


@pytest.fixture
def task():
    org = Organization.objects.create(name="Input recovery", slug="input-recovery")
    task = AgentTask.objects.create(organization=org)
    AgentTask.objects.filter(pk=task.pk).update(status=AgentTask.Status.RUNNING)
    task.refresh_from_db()
    return task


def send(task, key=None, message="nudge"):
    return queue_agent_task_input(task=task, message=message, client_request_id=key)


def gql(task, document, variables, org_id=None):
    with tenant_context(TenantContext(organization_id=org_id or task.organization_id)):
        return schema.execute_sync(
            document,
            variable_values={"task": str(task.guid), **variables},
            context_value=SimpleNamespace(user=None, request=None),
        )


MUTATION = """mutation Send($task: ID!, $key: String, $message: String!) {
  sendAgentTaskInput(taskId: $task, clientRequestId: $key, message: $message) {
    ok errors { code field } data { id clientRequestId message deliveredAt author }
  }
}"""
RECEIPT = """query Receipt($task: ID!, $key: String!) {
  agentTaskInputMessage(taskId: $task, clientRequestId: $key) {
    id clientRequestId message deliveredAt
  }
}"""


@pytest.mark.django_db
def test_lost_receipt_recovers_after_delivery_and_completion(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    key = str(uuid4())
    first = gql(task, MUTATION, {"key": key, "message": "  nudge  "})
    assert not first.errors
    admitted = first.data["sendAgentTaskInput"]["data"]
    assert admitted["clientRequestId"] == key
    assert [str(row.guid) for row in claim_pending_input(task)] == [admitted["id"]]
    AgentTask.objects.filter(pk=task.pk).update(status=AgentTask.Status.COMPLETED)
    receipt = gql(task, RECEIPT, {"key": key})
    assert not receipt.errors
    recovered = receipt.data["agentTaskInputMessage"]
    assert recovered["id"] == admitted["id"]
    assert recovered["deliveredAt"] is not None
    replay = gql(task, MUTATION, {"key": key, "message": "nudge"})
    assert not replay.errors
    assert replay.data["sendAgentTaskInput"]["data"]["id"] == admitted["id"]
    assert AgentTaskInputMessage.objects.count() == 1
    assert claim_pending_input(task) == []
    rejected = gql(task, MUTATION, {"key": str(uuid4()), "message": "new input"})
    assert rejected.data["sendAgentTaskInput"]["errors"][0]["code"] == "PRECONDITION"


@pytest.mark.django_db
def test_same_key_changed_message_and_deleted_receipt_never_reenqueue(task):
    key = str(uuid4())
    row = send(task, key)
    for body in ["different", "Nudge"]:
        with pytest.raises(AgentTaskInputError, match="already been used"):
            send(task, key, body)
    AgentTaskInputMessage.objects.filter(pk=row.pk).update(deleted_at=timezone.now())
    with pytest.raises(AgentTaskInputError, match="already been used"):
        send(task, key)
    with pytest.raises(IntegrityError), transaction.atomic():
        AgentTaskInputMessage.objects.create(
            agent_task=task,
            organization_id=task.organization_id,
            client_request_id=key,
            body="bypass service",
        )
    assert AgentTaskInputMessage.all_objects.count() == 1


@pytest.mark.django_db
def test_key_scope_and_legacy_unkeyed_inputs(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    key = str(uuid4())
    other = AgentTask.objects.create(organization_id=task.organization_id)
    AgentTask.objects.filter(pk=other.pk).update(status=AgentTask.Status.RUNNING)
    assert send(task, key).pk != send(other, key).pk
    legacy = """mutation Legacy($task: ID!) {
      sendAgentTaskInput(taskId: $task, message: "legacy") { ok data { id message } }
    }"""
    for _ in range(2):
        result = gql(task, legacy, {})
        assert not result.errors
        assert result.data["sendAgentTaskInput"]["ok"]
    assert AgentTaskInputMessage.objects.filter(client_request_id=None).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["", "bad", "x" * 500])
def test_invalid_key_never_queues(task, permission_resolver, key):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    result = gql(task, MUTATION, {"key": key, "message": "nudge"})
    assert not result.errors
    assert result.data["sendAgentTaskInput"]["errors"][0]["field"] == "clientRequestId"
    assert gql(task, RECEIPT, {"key": key}).data["agentTaskInputMessage"] is None
    assert not AgentTaskInputMessage.objects.exists()


@pytest.mark.django_db
def test_receipt_and_replay_require_permission_and_current_tenant(task, permission_resolver):
    key = str(uuid4())
    send(task, key)
    for permission in [None, Permission.AGENT_TASK_WATCH, Permission.AGENT_READ]:
        if permission:
            permission_resolver.grant(permission)
        assert gql(task, RECEIPT, {"key": key}).errors
        denied = gql(task, MUTATION, {"key": key, "message": "nudge"})
        assert denied.data["sendAgentTaskInput"]["errors"][0]["code"] == "PERMISSION_DENIED"
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    other = Organization.objects.create(name="Other", slug="other-input-recovery")
    assert gql(task, RECEIPT, {"key": key}, other.pk).data["agentTaskInputMessage"] is None
    denied = gql(task, MUTATION, {"key": key, "message": "nudge"}, other.pk)
    assert denied.data["sendAgentTaskInput"]["errors"][0]["code"] == "NOT_FOUND"
    assert AgentTaskInputMessage.objects.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("messages", [("nudge", "nudge"), ("nudge", "changed")])
def test_concurrent_enqueue_retries_admit_one_row(task, messages):
    barrier = Barrier(2)
    key = str(uuid4())

    def enqueue(message):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return str(send(task, key, message).guid)
        except AgentTaskInputError as exc:
            return exc.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(enqueue, messages))
    row = AgentTaskInputMessage.objects.get()
    if messages[0] == messages[1]:
        assert receipts == [str(row.guid), str(row.guid)]
    else:
        assert sorted(receipts) == sorted([str(row.guid), "precondition"])
    assert len(claim_pending_input(task)) == 1
    assert claim_pending_input(task) == []


@pytest.mark.django_db
def test_receipt_lookup_is_read_only_and_hides_soft_deleted_rows(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    key = str(uuid4())
    row = send(task, key)
    for _ in range(2):
        result = gql(task, RECEIPT, {"key": key})
        assert not result.errors
        assert result.data["agentTaskInputMessage"]["deliveredAt"] is None
    assert gql(task, RECEIPT, {"key": str(uuid4())}).data["agentTaskInputMessage"] is None
    row.refresh_from_db()
    assert row.delivered_at is None
    AgentTaskInputMessage.objects.filter(pk=row.pk).update(deleted_at=timezone.now())
    assert gql(task, RECEIPT, {"key": key}).data["agentTaskInputMessage"] is None
    assert AgentTaskInputMessage.all_objects.count() == 1


@pytest.mark.django_db
def test_stale_running_task_cannot_accept_new_input_after_completion(task):
    AgentTask.objects.filter(pk=task.pk).update(status=AgentTask.Status.COMPLETED)
    with pytest.raises(AgentTaskInputError, match="not accepting input"):
        send(task, str(uuid4()))
    assert not AgentTaskInputMessage.objects.exists()


@pytest.mark.django_db
def test_keyed_mutation_and_replay_keep_audit_action_and_target(task, permission_resolver):
    from core import mutations

    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    rows = []
    original = mutations._audit_writer
    mutations.register_audit_writer(rows.append)
    try:
        key = str(uuid4())
        for _ in range(2):
            result = gql(task, MUTATION, {"key": key, "message": "nudge"})
            assert not result.errors
            assert result.data["sendAgentTaskInput"]["ok"]
    finally:
        mutations.register_audit_writer(original)
    actions = [row for row in rows if row.action == "agents.task.send_input"]
    assert len(actions) == 2
    assert all(row.decision == "ALLOW" and row.target_id == str(task.guid) for row in actions)
    assert AgentTaskInputMessage.objects.count() == 1
