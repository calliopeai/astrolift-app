import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from django.db import close_old_connections
from django.test import Client
from django.utils import timezone

from astrolift_agents.models import AgentTask, AgentTaskInputReply, DispatcherInstance
from astrolift_agents.services.agent_task_events import MAX_TASK_EVENT_BYTES
from astrolift_agents.services.agent_task_input import queue_agent_task_input
from astrolift_agents.services.agent_task_requests import TaskInputRequestError, reply_to_request
from astrolift_identity.models import Organization
from config.schema import schema
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def task():
    org = Organization.objects.create(name="Requests", slug="requests")
    dispatcher = DispatcherInstance.objects.create(
        organization=org,
        name="Dispatcher",
        slug="requests-dispatcher",
        endpoint="https://dispatcher.example",
        api_key_hash=hashlib.sha256(b"fixture-key").hexdigest(),
        cloud=DispatcherInstance.Cloud.K8S_NATIVE,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
    )
    return AgentTask.objects.create(organization=org, dispatcher=dispatcher, status=AgentTask.Status.RUNNING)


def opening(sequence=1, message="approval-1"):
    return {
        "sequence": sequence,
        "turn_id": "turn-1",
        "message_id": message,
        "kind": "approval_required",
        "text": "Allow this command?",
        "request": {
            "version": 1,
            "kind": "approval",
            "tool": {"name": "Bash", "input": {"command": "git status"}},
        },
    }


def question(sequence=1, message="question-1"):
    value = opening(sequence, message)
    value.update(kind="input_required", text="Which branch?")
    value["request"] = {
        "version": 1,
        "kind": "question",
        "questions": [
            {"id": "branch", "text": "Which branch?", "options": [], "multiple": False},
            {
                "id": "checks",
                "text": "Which checks?",
                "options": [
                    {"label": "unit", "description": "Unit tests"},
                    {"label": "integration", "description": "Integration tests"},
                ],
                "multiple": True,
            },
        ],
    }
    return value


def post(task, **body):
    return Client().post(
        f"/api/dispatch/v1/agents/{task.guid}/callback/",
        content_type="application/json",
        data=json.dumps({"status": "running", **body}),
        HTTP_AUTHORIZATION="Bearer fixture-key",
    )


def gql_reply(task, response, sequence=1, organization_id=None):
    with tenant_context(TenantContext(organization_id=organization_id or task.organization_id)):
        return schema.execute_sync(
            """mutation Reply($task: ID!, $sequence: Int!, $response: JSON!) {
              replyAgentTaskInput(taskId: $task, requestSequence: $sequence, response: $response) {
                ok errors { code message } data { id requestSequence response authorLabel }
              }
            }""",
            variable_values={"task": str(task.guid), "sequence": sequence, "response": response},
            context_value=SimpleNamespace(user=None, request=None),
        )


def test_correlated_reply_survives_retry_without_consuming_steering(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    assert post(task, events=[]).json()["input_protocol_version"] == 1
    assert post(task, events=[opening()]).status_code == 200
    note = queue_agent_task_input(task=task, message="Unrelated next-turn steering")
    assert post(task, input_request=1).json()["input_response"] is None
    result = gql_reply(task, {"decision": "allow"})
    assert result.errors is None
    first = result.data["replyAgentTaskInput"]
    assert first["ok"], first
    assert gql_reply(task, {"decision": "allow"}).data["replyAgentTaskInput"]["data"] == first["data"]
    responses = [post(task, input_request=1).json()["input_response"] for _ in range(2)]
    assert (
        responses[0]
        == responses[1]
        == {
            "id": first["data"]["id"],
            "request_sequence": 1,
            "response": {"decision": "allow"},
        }
    )
    assert AgentTaskInputReply.objects.count() == 1
    note.refresh_from_db()
    assert note.delivered_at is None
    other = gql_reply(task, {"decision": "deny"}).data["replyAgentTaskInput"]
    assert not other["ok"]
    assert AgentTaskInputReply.objects.get().response == {"decision": "allow"}


def test_questions_retain_multiple_choices_and_free_text(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    assert post(task, events=[question()]).status_code == 200
    response = {"answers": {"branch": "feature/operator-choice", "checks": ["unit", "integration"]}}
    result = gql_reply(task, response)
    assert result.errors is None and result.data["replyAgentTaskInput"]["ok"]
    assert post(task, input_request=1).json()["input_response"]["response"] == response


def test_operator_read_permission_does_not_authorize_a_reply(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    post(task, events=[opening()])
    result = gql_reply(task, {"decision": "allow"})
    assert result.errors is None
    reply = result.data["replyAgentTaskInput"]
    assert not reply["ok"] and reply["data"] is None
    assert reply["errors"][0]["code"] == "PERMISSION_DENIED"
    assert not AgentTaskInputReply.objects.exists()


def test_reply_cannot_cross_organization_or_task(task, permission_resolver):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    post(task, events=[opening()])
    other_org = Organization.objects.create(name="Other", slug="other-request-org")
    result = gql_reply(task, {"decision": "allow"}, organization_id=other_org.pk)
    assert result.errors is None and not result.data["replyAgentTaskInput"]["ok"]
    sibling = AgentTask.objects.create(
        organization=task.organization,
        dispatcher=task.dispatcher,
        status=AgentTask.Status.RUNNING,
    )
    assert post(sibling, input_request=1).status_code == 404
    result = gql_reply(sibling, {"decision": "allow"})
    assert result.errors is None and not result.data["replyAgentTaskInput"]["ok"]
    assert not AgentTaskInputReply.objects.exists()


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {},
        {"decision": True},
        {"decision": {}},
        {"decision": "always"},
        {"decision": "allow", "extra": True},
        {"decision": "allow", "reason": "\x00"},
        {"decision": "deny", "reason": "x" * 4001},
        {"answers": {"branch": "main"}},
    ],
)
def test_bad_approval_reply_is_a_validation_error(task, permission_resolver, response):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    post(task, events=[opening()])
    result = gql_reply(task, response)
    if response is None:
        assert result.errors  # JSON! rejects null before invoking the resolver.
    else:
        assert result.errors is None
        assert not result.data["replyAgentTaskInput"]["ok"]
    assert not AgentTaskInputReply.objects.exists()


@pytest.mark.parametrize(
    "answers",
    [
        {},
        {"branch": "main"},
        {"branch": "main", "checks": "unit"},
        {"branch": ["main"], "checks": ["unit"]},
        {"branch": "main", "checks": []},
        {"branch": "main", "checks": ["unit", "unit"]},
        {"branch": "main", "checks": ["unit"], "unknown": "answer"},
    ],
)
def test_answers_must_match_the_request(task, permission_resolver, answers):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    post(task, events=[question()])
    result = gql_reply(task, {"answers": answers})
    assert result.errors is None and not result.data["replyAgentTaskInput"]["ok"]


def test_request_identity_and_payload_are_immutable(task):
    original = opening()
    assert post(task, events=[original]).status_code == 200
    changed = copy.deepcopy(original)
    changed["request"]["tool"]["input"]["command"] = "git reset --hard"
    assert post(task, events=[changed]).status_code == 409
    assert post(task, events=[opening(2)]).status_code == 409
    assert task.events.count() == 1
    assert post(task, events=[original]).status_code == 200


@pytest.mark.parametrize(
    "request_payload",
    [
        {},
        {"version": True, "kind": "approval", "tool": {"name": "Bash", "input": {}}},
        {"version": 1, "kind": "approval", "tool": {"name": "Bash", "input": []}},
        {"version": 1, "kind": "approval", "tool": {"name": "Bash", "input": {"bad": "\x00"}}},
        {"version": 1, "kind": "approval", "tool": {"name": "Bash", "input": {"bad": "\ud800"}}},
        {"version": 1, "kind": "approval", "tool": {"name": "Bash", "input": {"bad": float("nan")}}},
        {"version": 1, "kind": "approval", "tool": {"name": "Bash", "input": {"big": "x" * 16384}}},
    ],
)
def test_bad_request_rejects_the_whole_callback(task, request_payload):
    event = opening() | {"request": request_payload}
    result = post(task, events=[event], finding={"must": "not commit"})
    assert result.status_code in {400, 413}
    task.refresh_from_db()
    assert task.event_sequence == 0 and task.result is None
    assert not task.events.exists()


def test_reply_capacity_is_atomic_and_retry_does_not_charge_twice(task):
    assert post(task, events=[opening(), opening(2, "approval-2")]).status_code == 200
    reply_to_request(task=task, sequence=1, response={"decision": "allow"})
    task.refresh_from_db()
    charged_bytes = task.event_bytes
    reply_to_request(task=task, sequence=1, response={"decision": "allow"})
    task.refresh_from_db()
    assert task.event_bytes == charged_bytes
    task.event_bytes = MAX_TASK_EVENT_BYTES
    task.save(update_fields=["event_bytes"])
    with pytest.raises(TaskInputRequestError, match="capacity"):
        reply_to_request(task=task, sequence=2, response={"decision": "deny"})
    assert AgentTaskInputReply.objects.count() == 1
    task.refresh_from_db()
    assert task.event_bytes == MAX_TASK_EVENT_BYTES


def test_resolution_refuses_late_reply_and_reused_identity(task):
    assert post(task, events=[opening()]).status_code == 200
    resolution = {
        "sequence": 2,
        "turn_id": "turn-1",
        "message_id": "approval-1",
        "kind": "input_resolved",
        "text": "",
    }
    assert post(task, events=[resolution]).status_code == 200
    with pytest.raises(TaskInputRequestError, match="already resolved"):
        reply_to_request(task=task, sequence=1, response={"decision": "allow"})
    assert post(task, events=[opening(3)]).status_code == 409
    assert post(task, events=[resolution | {"sequence": 3}]).status_code == 409


def test_cancelled_task_refuses_new_answers_but_preserves_identical_receipt(task):
    post(task, events=[opening(), opening(2, "approval-2")])
    reply = reply_to_request(task=task, sequence=1, response={"decision": "deny"})
    task.transition_to(AgentTask.Status.CANCELLED)
    assert reply_to_request(task=task, sequence=1, response={"decision": "deny"}).pk == reply.pk
    with pytest.raises(TaskInputRequestError, match="not accepting"):
        reply_to_request(task=task, sequence=2, response={"decision": "allow"})
    data = post(task, input_request=1).json()
    assert data["continue"] is False and data["input_response"] is None


def test_soft_deleted_reply_cannot_be_replaced(task):
    post(task, events=[opening()])
    reply = reply_to_request(task=task, sequence=1, response={"decision": "deny"})
    reply.deleted_at = timezone.now()
    reply.save(update_fields=["deleted_at"])
    with pytest.raises(TaskInputRequestError, match="already has"):
        reply_to_request(task=task, sequence=1, response={"decision": "allow"})


def test_concurrent_decisions_have_one_winner(task):
    post(task, events=[opening()])

    def send(decision):
        close_old_connections()
        try:
            return reply_to_request(task=task, sequence=1, response={"decision": decision}).response
        except TaskInputRequestError as exc:
            return exc.status
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, ["allow", "deny"]))
    assert results.count(409) == 1
    assert AgentTaskInputReply.objects.count() == 1


@pytest.mark.parametrize("sequence", [True, 0, -1, 100001, {}, "1", None])
def test_invalid_callback_reference_changes_nothing(task, sequence):
    response = post(task, events=[opening()], input_request=sequence, finding={"must": "not commit"})
    assert response.status_code == 400
    task.refresh_from_db()
    assert task.event_sequence == 0 and task.result is None


def test_protocol_poll_cannot_consume_unrelated_steering_or_finish_task(task):
    post(task, events=[opening()])
    queue_agent_task_input(task=task, message="Leave this queued")
    assert post(task, input_request=1, input_intent="consume").status_code == 400
    assert post(task, input_request=1, status="completed", result="wrong").status_code == 400
    task.refresh_from_db()
    assert task.status == AgentTask.Status.RUNNING and task.result is None
    assert task.input_messages.get().delivered_at is None
