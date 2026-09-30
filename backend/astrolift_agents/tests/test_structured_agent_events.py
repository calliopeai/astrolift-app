"""Accept the shipped runner contract and project observed tool lifecycles."""

import copy

import pytest

from astrolift_agents.models import AgentTaskEvent
from astrolift_agents.services.agent_host_projection import chat_uri, snapshots
from astrolift_agents.tests import test_agent_task_requests as requests

pytestmark = pytest.mark.django_db(transaction=True)
task = requests.task


def event(sequence, kind, data, message="tool-2"):
    return {
        "sequence": sequence,
        "turn_id": "turn-1",
        "message_id": message,
        "kind": kind,
        "text": "",
        "data": data,
    }


def lifecycle():
    return [
        event(1, "turn_started", {"trigger": "task"}, "turn-1"),
        event(2, "tool_call_started", {"name": "Read"}),
        event(3, "tool_call_input", {"input": {"file_path": "README.md"}, "truncated": False}),
        event(4, "tool_call_result", {"output": "Project guide", "truncated": False}),
        event(5, "tool_call_completed", {"duration_ms": 10}),
        event(6, "turn_completed", {"stop_reason": "end_turn", "duration_ms": 20}, "turn-1"),
    ]


def test_negotiation_storage_retries_and_completed_tool_projection(task):
    assert requests.post(task, events=[]).json()["structured_event_protocol_version"] == 1
    first = requests.post(task, events=lifecycle())
    assert first.status_code == 200, first.content
    assert first.json()["event_sequence"] == 6
    assert requests.post(task, events=lifecycle()).status_code == 200
    assert AgentTaskEvent.objects.count() == 6
    assert AgentTaskEvent.objects.get(sequence=3).data["input"] == {"file_path": "README.md"}
    task.refresh_from_db()
    _, states = snapshots([task])
    chat = states[chat_uri(task)]
    assert chat["status"] == 1
    assert chat["turns"][0]["duration"] == 20
    tool = chat["turns"][0]["responseParts"][0]["toolCall"]
    assert tool["status"] == "completed"
    assert tool["success"] is True
    assert tool["content"] == [{"type": "text", "text": "Project guide"}]


@pytest.mark.parametrize(
    "change",
    ["extra", "wrong-type", "missing-data", "negative-duration", "secret-field", "oversize", "changed-retry"],
)
def test_malformed_or_conflicting_data_never_partially_commits(task, change):
    events = lifecycle()
    if change == "extra":
        events[0]["data"]["extra"] = "unsupported"
    elif change == "wrong-type":
        events[2]["data"]["input"] = "not-an-object"
    elif change == "missing-data":
        del events[1]["data"]
    elif change == "negative-duration":
        events[-1]["data"]["duration_ms"] = -1
    elif change == "secret-field":
        events[0]["unknown"] = "not-an-event-field"
    elif change == "oversize":
        events[2]["data"]["input"]["content"] = "x" * 9000
    else:
        assert requests.post(task, events=events).status_code == 200
        events[2]["data"]["input"]["file_path"] = "different.md"
    before = AgentTaskEvent.objects.count()
    response = requests.post(task, events=events)
    assert response.status_code in (400, 409, 413), response.content
    assert AgentTaskEvent.objects.count() == before


def test_existing_input_protocol_requests_accept_a_link_to_the_observed_call(task):
    events = lifecycle()[:3]
    approval = requests.opening(sequence=4)
    approval["data"] = {"call_id": "tool-2"}
    events.append(approval)
    assert requests.post(task, events=events).status_code == 200
    changed = copy.deepcopy(events)
    changed[-1]["data"]["call_id"] = "tool-other"
    assert requests.post(task, events=changed).status_code == 409
    task.refresh_from_db()
    _, states = snapshots([task])
    tool_parts = [
        part for part in states[chat_uri(task)]["activeTurn"]["responseParts"] if part["kind"] == "toolCall"
    ]
    assert len(tool_parts) == 1
    assert tool_parts[0]["toolCall"]["toolCallId"] == "tool-2"
    assert tool_parts[0]["toolCall"]["status"] == "pending-confirmation"
