"""A configured policy is enforced beneath AHP and GraphQL reply paths."""

from types import SimpleNamespace

import pytest

from astrolift_agents.models import AgentTaskInputReply
from astrolift_agents.services import agent_host_policy as policy
from astrolift_agents.services.agent_host_projection import chat_uri, snapshots
from astrolift_agents.services.agent_task_requests import TaskInputRequestError, reply_to_request
from astrolift_agents.tests import test_agent_host as host_tests
from astrolift_operations.models import ZentinelleConnection
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db(transaction=True)
world = host_tests.world


def setup_policy(world):
    connection = ZentinelleConnection.objects.create(
        organization=world.org, base_url="https://governance.example", tenant_ids=["tenant-a"]
    )
    task = world.task
    task.model_gateway_connection, task.model_gateway_agent_id = connection, "agent-task-1"
    task.save()
    return host_tests.add_event(
        task,
        "Review shell",
        "approval_required",
        {"version": 1, "kind": "approval", "tool": {"name": "Bash", "input": {"command": "pwd"}}},
    )


def test_denial_cannot_be_overridden_by_a_controller(world, monkeypatch):
    event = setup_policy(world)
    monkeypatch.setattr(
        policy,
        "_install_call",
        lambda *args: SimpleNamespace(ok=True, body={"decision": "deny", "reason": "No shell"}),
    )
    with pytest.raises(TaskInputRequestError, match="denied"):
        reply_to_request(
            task=world.task, sequence=event.sequence, response={"decision": "allow"}, author=world.other
        )
    assert not AgentTaskInputReply.objects.exists()
    # Even an outage does not prevent a controller from rejecting a tool.
    reply_to_request(
        task=world.task, sequence=event.sequence, response={"decision": "deny"}, author=world.other
    )
    with tenant_context(host_tests.tenant(world)):
        _, states = snapshots([world.task])
    tool = states[chat_uri(world.task)]["activeTurn"]["responseParts"][0]["toolCall"]
    assert tool["status"] == "cancelled"
    assert AgentTaskInputReply.objects.count() == 1


def test_human_hold_is_decided_then_re_evaluated(world, monkeypatch):
    event = setup_policy(world)
    calls = []

    def remote(connection, method, path, body):
        calls.append((path, body))
        if path.endswith("/decision"):
            return SimpleNamespace(ok=True, body={"approval_token": "signed-single-use-grant"})
        approved = body["context"].get("approval_token") == "signed-single-use-grant"
        return SimpleNamespace(
            ok=True, body={"decision": "allow" if approved else "ask", "approval": {"request_id": "held-1"}}
        )

    monkeypatch.setattr(policy, "_install_call", remote)
    reply_to_request(
        task=world.task, sequence=event.sequence, response={"decision": "allow"}, author=world.other
    )
    assert len(calls) == 3
    assert calls[1][1]["actor_id"] == str(world.other.pk)
    assert calls[-1][1]["context"]["session_id"].endswith(str(world.task.guid))
    assert AgentTaskInputReply.objects.get().response["decision"] == "allow"


def test_policy_outage_keeps_a_tool_blocked(world, monkeypatch):
    event = setup_policy(world)
    monkeypatch.setattr(policy, "_install_call", lambda *args: SimpleNamespace(ok=False, body={}))
    with pytest.raises(TaskInputRequestError, match="blocked"):
        reply_to_request(
            task=world.task, sequence=event.sequence, response={"decision": "allow"}, author=world.other
        )
    assert not AgentTaskInputReply.objects.exists()
    with tenant_context(host_tests.tenant(world)):
        _, states = snapshots([world.task])
    assert states[chat_uri(world.task)]["status"] == 24


def test_projection_applies_an_automatic_policy_denial_once(world, monkeypatch):
    setup_policy(world)
    monkeypatch.setattr(
        policy,
        "_install_call",
        lambda *args: SimpleNamespace(ok=True, body={"decision": "deny", "reason": "No shell"}),
    )
    with tenant_context(host_tests.tenant(world)):
        snapshots([world.task])
        snapshots([world.task])
    assert AgentTaskInputReply.objects.count() == 1
    assert AgentTaskInputReply.objects.get().response["decision"] == "deny"
