import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from django.db import close_old_connections
from django.test import Client
from graphql import GraphQLError

from astrolift_agents.models import AgentTask, AgentTaskEvent, DispatcherInstance
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.services.agent_task_events import MAX_EVENT_BYTES, MAX_TASK_EVENT_BYTES
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def task():
    org = Organization.objects.create(name="Events", slug="events")
    dispatcher = DispatcherInstance.objects.create(
        organization=org,
        name="Dispatcher",
        slug="events-dispatcher",
        endpoint="https://dispatcher.example",
        api_key_hash=hashlib.sha256(b"fixture-key").hexdigest(),
        cloud=DispatcherInstance.Cloud.K8S_NATIVE,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
    )
    return AgentTask.objects.create(organization=org, dispatcher=dispatcher, status=AgentTask.Status.RUNNING)


def event(sequence=1, text="Hello", kind="assistant_delta", turn="turn-1", message="message-1"):
    return {"sequence": sequence, "turn_id": turn, "message_id": message, "kind": kind, "text": text}


def post(task, events, **body):
    return Client().post(
        f"/api/dispatch/v1/agents/{task.guid}/callback/",
        data=json.dumps({"status": "running", "events": events, **body}),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer fixture-key",
    )


def read(task, **options):
    with tenant_context(TenantContext(organization_id=task.organization_id)):
        return AgentsQuery().agent_task_events(
            info=SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            org_id=str(task.organization.guid),
            task_id=str(task.guid),
            **options,
        )


def test_replay_and_retry_preserve_order_and_do_not_duplicate(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    batch = [event(), event(2, " world"), event(3, "", "message_end")]
    assert post(task, batch).json()["event_sequence"] == 3
    task.refresh_from_db()
    version = task.version
    assert post(task, batch).json()["event_sequence"] == 3
    task.refresh_from_db()
    assert task.version == version
    first = read(task, limit=2)
    rest = read(task, after=first[-1].sequence)
    assert [row.sequence for row in first + rest] == [1, 2, 3]
    assert "".join(row.text for row in first + rest) == "Hello world"
    assert AgentTaskEvent.objects.count() == 3
    assert read(task, after=3) == []


def test_overlapping_retry_appends_only_unseen_suffix(task):
    assert post(task, [event()]).status_code == 200
    assert post(task, [event(), event(2, " again")]).json()["event_sequence"] == 2
    assert list(task.events.values_list("text", flat=True)) == ["Hello", " again"]


def test_conflicting_retry_is_atomic_with_terminal_result(task):
    post(task, [event()])
    response = post(task, [event(text="replacement"), event(2)], status="completed", result="done")
    assert response.status_code == 409
    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING and task.result is None
    assert task.events.count() == 1 and task.event_sequence == 1


def test_gap_rejects_entire_callback_including_findings(task):
    response = post(task, [event(2)], finding={"value": "must not persist"})
    assert response.status_code == 409
    task.refresh_from_db()
    assert task.result is None and task.event_sequence == 0 and task.events.count() == 0


@pytest.mark.parametrize(
    "events",
    [
        None,
        {},
        [event(sequence=True)],
        [event(sequence=0)],
        [event(sequence=100001)],
        [event(), event(3)],
        [event(kind="thinking")],
        [event(turn="../bad")],
        [event(text={})],
        [event(text="\ud800")],
        [event(text="before\x00after")],
        [event() | {"extra": 1}],
        [event(i + 1) for i in range(65)],
    ],
)
def test_malformed_events_do_not_write(task, events):
    assert post(task, events).status_code == 400
    assert not task.events.exists()


def test_event_limit_counts_utf8_bytes(task):
    response = post(task, [event(text="🌲" * (MAX_EVENT_BYTES // 4 + 1))])
    assert response.status_code == 413
    assert not task.events.exists()


def test_aggregate_limit_does_not_partially_commit_batch(task):
    AgentTask.objects.filter(pk=task.pk).update(event_bytes=MAX_TASK_EVENT_BYTES - 6)
    response = post(task, [event(text="12345"), event(2, "12")])
    assert response.status_code == 413
    assert not task.events.exists()


def test_terminal_events_commit_with_result_and_survive_reopen(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    response = post(task, [event(), event(2, "", "message_end")], status="completed", result="Hello")
    assert response.status_code == 200 and response.json()["event_sequence"] == 2
    task.refresh_from_db()
    assert task.status == AgentTask.Status.COMPLETED and task.result == {"output": "Hello"}
    assert [row.sequence for row in read(task)] == [1, 2]
    assert post(task, [event(), event(2, "", "message_end")]).status_code == 200
    assert post(task, [event(3)]).status_code == 409


def test_fast_provisioning_agent_can_report_first_events(task):
    AgentTask.objects.filter(pk=task.pk).update(status=AgentTask.Status.PROVISIONING)
    response = post(task, [event()])
    assert response.status_code == 200 and response.json()["continue"] is True
    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING


def test_input_and_terminal_events_keep_identity(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    batch = [
        event(kind="terminal_delta"),
        event(2, "Allow edit?", "approval_required", message="approval-1"),
        event(3, "", "input_resolved", message="approval-1"),
        event(4, "Next answer", turn="turn-2"),
    ]
    assert post(task, batch).status_code == 200
    rows = read(task)
    assert [row.kind for row in rows] == [row["kind"] for row in batch]
    assert rows[1].message_id == rows[2].message_id and rows[3].turn_id == "turn-2"


def test_feed_denies_missing_permission_and_foreign_tenant(task, permission_resolver):
    post(task, [event()])
    with pytest.raises(PermissionDenied):
        read(task)
    permission_resolver.grant(Permission.AGENT_READ)
    foreign = Organization.objects.create(name="Other", slug="other")
    with tenant_context(TenantContext(organization_id=foreign.pk)):
        rows = AgentsQuery().agent_task_events(
            info=SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            org_id=str(foreign.guid),
            task_id=str(task.guid),
        )
    assert rows == []


def test_missing_history_and_invalid_cursor_are_explicit(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    post(task, [event(), event(2)])
    with pytest.raises(GraphQLError, match="cursor"):
        read(task, after=3)
    task.events.first().soft_delete()
    with pytest.raises(GraphQLError, match="incomplete"):
        read(task)


def test_concurrent_retries_commit_one_copy(task):
    def send():
        close_old_connections()
        try:
            return post(task, [event()]).status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert results == [200, 200]
    assert task.events.count() == 1


def test_task_callback_token_cannot_write_other_tasks_or_survive_cancel(task):
    token = "alft_cb_event_fixture"
    AgentTask.objects.filter(pk=task.pk).update(
        callback_token_hash=hashlib.sha256(token.encode()).hexdigest()
    )
    other = AgentTask.objects.create(organization=task.organization, status=AgentTask.Status.RUNNING)

    def send(target):
        return Client().post(
            f"/api/dispatch/v1/agents/{target.guid}/callback/",
            data=json.dumps({"events": [event()]}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    assert send(other).status_code == 401
    assert send(task).status_code == 200
    task.refresh_from_db()
    task.transition_to(AgentTask.Status.CANCELLED)
    assert send(task).status_code == 401
    assert task.events.count() == 1 and other.events.count() == 0


def test_graphql_feed_has_typed_identity_and_cursor(task, permission_resolver):
    from config.schema import schema

    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.APP_READ)
    post(task, [event(), event(2, " next")])
    with tenant_context(TenantContext(organization_id=task.organization_id)):
        result = schema.execute_sync(
            """query($org: ID!, $task: ID!) {
              agentTaskEvents(orgId: $org, taskId: $task, after: 1) {
                sequence turnId messageId kind text createdAt
              }
              agentTask(id: $task) { eventSequence }
            }""",
            variable_values={"org": str(task.organization.guid), "task": str(task.guid)},
            context_value=SimpleNamespace(user=None, request=None),
        )
    assert result.errors is None
    assert result.data["agentTask"]["eventSequence"] == 2
    rows = result.data["agentTaskEvents"]
    assert len(rows) == 1 and rows[0]["sequence"] == 2 and rows[0]["text"] == " next"


def test_empty_batch_probes_support_without_writing(task):
    version = task.version
    assert post(task, []).json()["event_sequence"] == 0
    task.refresh_from_db()
    assert task.version == version and not task.events.exists()
