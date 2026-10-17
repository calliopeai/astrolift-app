"""Agents message people, people answer the agent (#2434).

Real Postgres and real role bindings: who can be addressed and who can read a
message is decided by agent.read on the agent's app, as each person, the same
rule as the agent list. The run authenticates with its own callback key; the
Temporal client is a recorder.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid

import pytest
from django.test import Client
from django.utils import timezone

from astrolift_agents.message_schema import (
    AgentMessagesMutation,
    AgentMessagesQuery,
    ReplyToAgentMessageInput,
    _new_for_identity,
)
from astrolift_agents.models import (
    AgentMessage,
    AgentMessageRecipient,
    AgentMessageReply,
    AgentTask,
    AgentTaskEvent,
    AgentTaskInputMessage,
    AgentTaskInputReply,
)
from astrolift_agents.services import agent_messages
from astrolift_agents.tests.test_run_agent_brains_2432 import _agent, _as
from astrolift_identity.abac import RequestAttributes
from astrolift_identity.models import Member, RoleBinding
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

pytestmark = pytest.mark.django_db

CONTROLLER = "https://astro.example.net"
LAUNCHER = [
    Permission.AGENT_DISPATCH,
    Permission.AGENT_READ,
    Permission.AGENT_TASK_SEND_INPUT,
    Permission.APP_READ,
]
READER = [Permission.AGENT_READ, Permission.APP_READ]


@pytest.fixture(autouse=True)
def _env(monkeypatch, settings):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))
    settings.PLATFORM_API_URL = CONTROLLER
    settings.FRONTEND_URL = CONTROLLER


@pytest.fixture
def started(monkeypatch):
    calls = []

    def start(name, args, *, workflow_id, task_queue=None, replace_running=True):
        from astrolift_workflows.client import WorkflowHandle

        calls.append((name, args, workflow_id))
        return WorkflowHandle(workflow_id=workflow_id, run_id="r", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", start)
    return calls


@pytest.fixture
def audit(monkeypatch):
    rows = []
    monkeypatch.setattr("core.mutations._audit_writer", rows.append)
    return rows


def _member(world, user):
    Member.objects.create(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=world.org.pk, is_active=True)
    return user


def _person(world, suffix, permissions, *, team=None):
    user = _member(world, make_user(suffix))
    if permissions:
        bind_role(
            user,
            permissions=permissions,
            kind="TEAM",
            scope_id=(team or world.medops).pk,
            slug=f"role-{suffix}",
        )
    return user


@pytest.fixture
def world():
    world = ScopeWorld("am2434")
    world.user = _person(world, "am2434", LAUNCHER)
    world.reader = _person(world, "rd2434", READER)
    world.outsider = _person(world, "ou2434", READER, team=world.platform)
    world.agent = _agent(world)
    return world


@pytest.fixture
def other():
    other = ScopeWorld("amx2434")
    other.user = _person(other, "amx2434", LAUNCHER)
    other.agent = _agent(other)
    return other


def _task(world, *, status=AgentTask.Status.RUNNING, user=None):
    key = "alft_cb_" + secrets.token_urlsafe(24)
    task = AgentTask.objects.create(
        organization=world.org,
        agent_definition=world.agent,
        status=status,
        triggered_by_user=user or world.user,
        callback_token_hash=hashlib.sha256(key.encode()).hexdigest()
        if status == AgentTask.Status.RUNNING
        else "",
    )
    return task, key


def _send(task, key, **body):
    return Client().post(
        f"/api/dispatch/v1/agents/{task.guid}/messages/",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {key}",
    )


def _inbox(world, user, **kwargs):
    with _as(world, user) as info:
        return AgentMessagesQuery().astrolift_agent_messages(info, **kwargs)


def _reply(world, user, message_id, **kwargs):
    with _as(world, user) as info:
        return AgentMessagesMutation().reply_to_agent_message(
            info, input=ReplyToAgentMessageInput(message_id=str(message_id), **kwargs)
        )


# ---------------------------------------------------------------------------
# Sending and addressing
# ---------------------------------------------------------------------------


def test_a_run_messages_the_person_who_launched_it_by_default(
    world, audit, django_capture_on_commit_callbacks
):
    task, key = _task(world)

    with django_capture_on_commit_callbacks(execute=True):
        response = _send(task, key, kind="update", title="Halfway", body="Indexed **40%** of the repo.")

    assert response.status_code == 201, response.content
    message = AgentMessage.objects.get(guid=response.json()["id"])
    assert (message.kind, message.source, message.thread_id) == ("update", "agent", task.guid)
    assert list(message.recipients.values_list("user_id", flat=True)) == [world.user.pk]
    assert [row.action for row in audit if row.target_kind == "AgentMessage"] == ["agent.message.send"]
    from astrolift_operations.models import Event

    event = Event.objects.get(event_type="agent.message.sent")
    assert event.payload["recipient_user_ids"] == [world.user.pk]
    assert event.payload["message_id"] == str(message.guid)


def test_addressing_someone_outside_the_agents_audience_refuses_the_whole_send(world):
    task, key = _task(world)

    response = _send(task, key, body="hi", to=[world.reader.email, world.outsider.email, "nobody@acme.test"])

    assert response.status_code == 403
    error = response.json()["error"]
    assert world.outsider.email in error and "nobody@acme.test" in error
    assert world.reader.email not in error
    assert not AgentMessage.objects.exists()


def test_a_team_reaches_its_members_who_can_read_the_agent(world):
    for user in (world.user, world.reader):
        Member.objects.create(user=user, scope_kind=Member.ScopeKind.TEAM, scope_id=world.medops.pk)
    Member.objects.create(user=world.outsider, scope_kind=Member.ScopeKind.TEAM, scope_id=world.platform.pk)
    task, key = _task(world)

    ok = _send(task, key, body="standup", teams=[world.medops.slug])
    refused = _send(task, key, body="standup", teams=[world.platform.slug])

    assert ok.status_code == 201
    message = AgentMessage.objects.get(guid=ok.json()["id"])
    assert set(message.recipients.values_list("user_id", flat=True)) == {world.user.pk, world.reader.pk}
    assert refused.status_code == 403 and f"team:{world.platform.slug}" in refused.json()["error"]


def test_another_orgs_person_cannot_be_addressed(world, other):
    task, key = _task(world)

    response = _send(task, key, body="hi", to=[other.user.email])

    assert response.status_code == 403


def test_only_the_runs_own_key_sends_and_only_while_it_runs(world):
    task, key = _task(world)
    wrong = Client().post(
        f"/api/dispatch/v1/agents/{task.guid}/messages/",
        data="{}",
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer alft_cb_not-this-run",
    )
    assert wrong.status_code == 401

    AgentTask.objects.filter(pk=task.pk).update(status=AgentTask.Status.COMPLETED)
    assert _send(task, key, body="late").status_code == 409


def test_bodies_are_redacted_before_they_are_stored(world):
    from astrolift_agents.models import AgentEnvironmentSpec

    spec = AgentEnvironmentSpec.objects.create(
        organization=world.org,
        slug="triage-spec",
        secret_refs=[{"uri": "arn:aws:secretsmanager:us-east-1:1:secret:gh-token", "env_var": "GH"}],
    )
    task, key = _task(world)
    AgentTask.objects.filter(pk=task.pk).update(environment_spec=spec)

    response = _send(
        task,
        key,
        body="token alft_at_abcdefghijklmnop and arn:aws:secretsmanager:us-east-1:1:secret:gh-token key AKIAABCDEFGHIJKLMNOP",
    )

    body = AgentMessage.objects.get(guid=response.json()["id"]).body
    assert "alft_at_" not in body and "secretsmanager" not in body and "AKIA" not in body
    assert body.count("[redacted]") == 3


def test_the_agent_is_rate_limited(world, settings):
    settings.AGENT_MESSAGE_RATE_PER_MINUTE = 2
    task, key = _task(world)

    codes = [_send(task, key, body=f"n{i}").status_code for i in range(3)]

    assert codes == [201, 201, 429]


def test_a_retry_with_the_same_request_id_sends_once(world):
    task, key = _task(world)
    request_id = str(uuid.uuid4())

    first = _send(task, key, body="once", client_request_id=request_id)
    second = _send(task, key, body="once", client_request_id=request_id)

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert AgentMessage.objects.count() == 1


def test_url_attachments_must_point_at_this_install(world):
    task, key = _task(world)

    outside = _send(task, key, body="x", attachments=[{"kind": "url", "ref": "https://evil.example/x"}])
    inside = _send(
        task,
        key,
        body="x",
        attachments=[
            {"kind": "url", "ref": f"{CONTROLLER}/agents/runs/1"},
            {"kind": "artifact", "ref": "report.pdf"},
            {"kind": "citation", "ref": "kb:doc-12"},
        ],
    )

    assert outside.status_code == 400
    assert inside.status_code == 201


def test_the_runs_mcp_server_sends_as_the_run(world):
    task, key = _task(world)
    url = f"/api/dispatch/v1/agents/{task.guid}/mcp/"

    def rpc(method, params=None):
        return (
            Client()
            .post(
                url,
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {key}",
            )
            .json()
        )

    tools = [tool["name"] for tool in rpc("tools/list")["result"]["tools"]]
    sent = rpc("tools/call", {"name": "astrolift_message_user", "arguments": {"body": "via mcp"}})
    refused = rpc(
        "tools/call",
        {"name": "astrolift_message_user", "arguments": {"body": "x", "to": [world.outsider.email]}},
    )

    assert tools == ["astrolift_message_user", "astrolift_get_message_replies"]
    assert sent["result"]["isError"] is False
    assert AgentMessage.objects.filter(guid=sent["result"]["structuredContent"]["id"]).exists()
    assert refused["result"]["isError"] is True


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_read_state_is_per_recipient(world):
    for user in (world.user, world.reader):
        Member.objects.create(user=user, scope_kind=Member.ScopeKind.TEAM, scope_id=world.medops.pk)
    task, key = _task(world)
    _send(task, key, body="one", teams=[world.medops.slug])
    _send(task, key, body="two", teams=[world.medops.slug])

    page = _inbox(world, world.user)
    assert (page.unread_count, [m.body for m in page.items]) == (2, ["two", "one"])
    assert all(m.delivered_at is not None for m in page.items)

    with _as(world, world.user) as info:
        marked = AgentMessagesMutation().mark_agent_messages_read(info, ids=[page.items[0].id])
    assert marked.ok and marked.data.marked == 1
    assert _inbox(world, world.user).unread_count == 1
    assert _inbox(world, world.reader).unread_count == 2
    assert [m.body for m in _inbox(world, world.user, unread_only=True).items] == ["one"]


def test_threads_group_by_run(world):
    first, key1 = _task(world)
    second, key2 = _task(world)
    _send(first, key1, body="a")
    _send(first, key1, body="b")
    _send(second, key2, body="c")

    with _as(world, world.user) as info:
        threads = AgentMessagesQuery().astrolift_agent_message_threads(info)

    assert [(str(t.thread_id), t.message_count, t.unread_count) for t in threads.items] == [
        (str(second.guid), 1, 1),
        (str(first.guid), 2, 2),
    ]
    assert threads.items[1].last_message.body == "b"
    one = _inbox(world, world.user, thread_id=str(first.guid))
    assert [m.body for m in one.items] == ["b", "a"]


def test_another_org_sees_nothing_and_cannot_reply(world, other):
    task, key = _task(world)
    message_id = _send(task, key, body="ours").json()["id"]

    assert _inbox(other, other.user).items == []
    result = _reply(other, other.user, message_id, body="hi")
    assert not result.ok and result.errors[0].code == "NOT_FOUND"


def test_losing_access_mid_thread_stops_receiving_and_replying(world):
    task, key = _task(world)
    message_id = _send(task, key, body="first", to=[world.reader.email]).json()["id"]
    assert len(_inbox(world, world.reader).items) == 1

    # Still reads agents elsewhere in the org, no longer this one.
    RoleBinding.objects.filter(user=world.reader).delete()
    bind_role(
        world.reader, permissions=READER, kind="TEAM", scope_id=world.platform.pk, slug="role-rd-platform"
    )

    assert _inbox(world, world.reader).items == []
    assert not _reply(world, world.reader, message_id, body="still here?").ok
    assert _send(task, key, body="second", to=[world.reader.email]).status_code == 403


# ---------------------------------------------------------------------------
# Replies go back to the agent
# ---------------------------------------------------------------------------


def test_a_reply_reaches_a_live_run_on_its_input_channel(world):
    task, key = _task(world)
    message_id = _send(
        task,
        key,
        kind="question",
        body="Which branch?",
        options=[{"id": "main", "label": "main"}, {"id": "dev", "label": "dev"}],
    ).json()["id"]

    result = _reply(world, world.user, message_id, choices=["dev"], body="dev please")

    assert result.ok, result.errors
    assert result.data.route == "live_run"
    queued = AgentTaskInputMessage.objects.get(agent_task=task)
    assert message_id in queued.body and "Choice: dev" in queued.body
    callback = (
        Client()
        .post(
            f"/api/dispatch/v1/agents/{task.guid}/callback/",
            data=json.dumps({"status": "running", "input_intent": "consume"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {key}",
        )
        .json()
    )
    assert [entry["id"] for entry in callback["pending_input"]] == [str(queued.guid)]
    page = _inbox(world, world.user)
    assert page.items[0].answerable is False
    assert page.items[0].replies[0].delivered_at is not None


def test_a_reply_after_the_run_ended_wakes_the_agent(world, started, django_capture_on_commit_callbacks):
    task, key = _task(world)
    message_id = _send(task, key, kind="question", body="Retry tonight?").json()["id"]
    AgentTask.objects.filter(pk=task.pk).update(status=AgentTask.Status.COMPLETED, callback_token_hash="")

    with django_capture_on_commit_callbacks(execute=True):
        result = _reply(world, world.user, message_id, body="yes")

    assert result.ok, result.errors
    persisted = AgentMessageReply.objects.get(guid=str(result.data.id))
    assert persisted.route == "woke" and persisted.wake_task_id is not None
    woken = persisted.wake_task
    assert woken.dispatch_input["agent_message_reply"]["body"] == "yes"
    assert woken.triggered_by_user_id == world.user.pk
    assert len(started) == 1


def test_a_reply_the_person_cannot_run_waits_for_the_agents_next_run(
    world, started, django_capture_on_commit_callbacks
):
    task, key = _task(world)
    message_id = _send(task, key, body="FYI", to=[world.reader.email]).json()["id"]
    AgentTask.objects.filter(pk=task.pk).update(status=AgentTask.Status.COMPLETED, callback_token_hash="")

    with django_capture_on_commit_callbacks(execute=True):
        result = _reply(world, world.reader, message_id, body="thanks")

    assert result.ok and result.data.route == "queued" and started == []
    later, later_key = _task(world)

    def collect():
        return (
            Client()
            .get(f"/api/dispatch/v1/agents/{later.guid}/messages/", HTTP_AUTHORIZATION=f"Bearer {later_key}")
            .json()
        )

    first, again = collect(), collect()
    assert [(r["message_id"], r["body"]) for r in first["replies"]] == [(message_id, "thanks")]
    assert again["replies"] == []
    assert AgentMessageReply.objects.get().delivered_at is not None


def test_an_approval_goes_through_the_runs_gate(world):
    task, key = _task(world)
    AgentTaskEvent.objects.create(
        organization=world.org,
        agent_task=task,
        sequence=1,
        turn_id="t1",
        message_id="m1",
        kind=AgentTaskEvent.Kind.APPROVAL_REQUIRED,
        text="deploy to prod",
        request={"version": 1, "kind": "approval", "tool": {"name": "deploy", "input": {}}},
    )
    AgentTask.objects.filter(pk=task.pk).update(event_sequence=1)
    Member.objects.create(user=world.reader, scope_kind=Member.ScopeKind.TEAM, scope_id=world.medops.pk)

    no_gate = _send(task, key, kind="approval", body="May I deploy?")
    message_id = _send(
        task,
        key,
        kind="approval",
        body="May I deploy?",
        request_sequence=1,
        to=[world.user.email, world.reader.email],
    ).json()["id"]

    assert no_gate.status_code == 400
    refused = _reply(world, world.reader, message_id, decision="allow")
    assert not refused.ok and refused.errors[0].code == "PERMISSION_DENIED"
    approved = _reply(world, world.user, message_id, decision="allow", body="go")
    assert approved.ok, approved.errors
    assert approved.data.route == "gate"
    gate = AgentTaskInputReply.objects.get(request_event__agent_task=task)
    assert gate.response == {"decision": "allow", "reason": "go"}


def test_expired_questions_take_no_answer(world):
    task, key = _task(world)
    message_id = _send(task, key, kind="question", body="?", expires_in_seconds=60).json()["id"]
    AgentMessage.objects.filter(guid=message_id).update(expires_at=timezone.now())

    result = _reply(world, world.user, message_id, body="late")

    assert not result.ok and result.errors[0].code == "PRECONDITION"


def test_a_read_only_token_cannot_approve_for_a_controller(world):
    from astrolift_identity.api_tokens import SCOPE_READ_APPS
    from astrolift_identity.models import ApiToken

    task, key = _task(world)
    AgentTaskEvent.objects.create(
        organization=world.org,
        agent_task=task,
        sequence=1,
        turn_id="t1",
        message_id="m1",
        kind=AgentTaskEvent.Kind.APPROVAL_REQUIRED,
        text="deploy",
        request={"version": 1, "kind": "approval", "tool": {"name": "deploy", "input": {}}},
    )
    AgentTask.objects.filter(pk=task.pk).update(event_sequence=1)
    message_id = _send(task, key, kind="approval", body="May I deploy?", request_sequence=1).json()["id"]
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="read-only",
        token_hash=uuid.uuid4().hex,
        scopes=[SCOPE_READ_APPS],
    )

    with _as(world, token=token) as info:
        result = AgentMessagesMutation().reply_to_agent_message(
            info, input=ReplyToAgentMessageInput(message_id=message_id, decision="allow")
        )

    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    assert not AgentTaskInputReply.objects.filter(request_event__agent_task=task).exists()
    assert not AgentMessageReply.objects.exists()


@pytest.mark.parametrize("gate", [False, True])
def test_a_failed_reply_insert_rolls_back_delivery(world, monkeypatch, gate):
    task, key = _task(world)
    payload = {"kind": "question", "body": "Continue?"}
    if gate:
        AgentTaskEvent.objects.create(
            organization=world.org,
            agent_task=task,
            sequence=1,
            turn_id="t1",
            message_id="m1",
            kind=AgentTaskEvent.Kind.APPROVAL_REQUIRED,
            text="deploy",
            request={"version": 1, "kind": "approval", "tool": {"name": "deploy", "input": {}}},
        )
        AgentTask.objects.filter(pk=task.pk).update(event_sequence=1)
        payload.update(kind="approval", request_sequence=1)
    message_id = _send(task, key, **payload).json()["id"]

    def fail(**kwargs):
        raise RuntimeError("reply persistence failed")

    monkeypatch.setattr(AgentMessageReply.objects, "create", fail)
    result = _reply(world, world.user, message_id, body="continue", decision="allow" if gate else None)
    assert not result.ok and result.errors[0].code == "INTERNAL"
    assert not AgentTaskInputMessage.objects.filter(agent_task=task).exists()
    assert not AgentTaskInputReply.objects.filter(request_event__agent_task=task).exists()


def test_a_retried_reply_delivers_one_redacted_input(world):
    task, key = _task(world)
    message_id = _send(task, key, kind="question", body="Continue?").json()["id"]
    request_id = str(uuid.uuid4())
    body = "continue alft_at_abcdefghijklmnop"

    first = _reply(world, world.user, message_id, body=body, client_request_id=request_id)
    second = _reply(world, world.user, message_id, body=body, client_request_id=request_id)

    assert first.ok and second.ok
    assert first.data.id == second.data.id
    queued = AgentTaskInputMessage.objects.get(agent_task=task)
    assert "alft_at_" not in queued.body and "[redacted]" in queued.body
    assert "alft_at_" not in AgentMessageReply.objects.get().body
    conflict = _reply(world, world.user, message_id, body="a different answer", client_request_id=request_id)
    assert not conflict.ok and conflict.errors[0].code == "PRECONDITION"
    assert AgentTaskInputMessage.objects.filter(agent_task=task).count() == 1


def test_a_full_size_reply_reaches_the_live_run_without_truncation(world):
    task, key = _task(world)
    message_id = _send(task, key, kind="question", body="Continue?").json()["id"]
    body = "z" * 4000

    result = _reply(world, world.user, message_id, body=body)

    assert result.ok, result.errors
    assert AgentTaskInputMessage.objects.get(agent_task=task).body == body


def test_a_token_revoked_during_subscription_stops_receiving(world):
    from astrolift_identity.api_tokens import SCOPE_READ_APPS
    from astrolift_identity.models import ApiToken

    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="inbox",
        token_hash=uuid.uuid4().hex,
        scopes=[SCOPE_READ_APPS],
    )
    tenant = TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)
    attributes = RequestAttributes(actor_user_id=world.user.pk)
    _, watermark = _new_for_identity(tenant, token, attributes, None)
    task, key = _task(world)
    _send(task, key, body="waiting")
    ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)

    items, unchanged = _new_for_identity(tenant, token, attributes, watermark)

    assert items is None and unchanged == watermark


def test_agent_messaging_is_advertised_by_the_public_handshake():
    from config.schema_public import schema_public

    result = schema_public.execute_sync("{ astroliftServerInfo { capabilities } }")

    assert not result.errors
    assert "agents.messages" in result.data["astroliftServerInfo"]["capabilities"]


def test_platform_question_options_redact_credentials(world):
    task, key = _task(world)
    credential = "alft_at_abcdefghijklmnop"
    event = AgentTaskEvent.objects.create(
        organization=world.org,
        agent_task=task,
        sequence=1,
        turn_id="t1",
        message_id="m1",
        kind=AgentTaskEvent.Kind.INPUT_REQUIRED,
        request={
            "version": 1,
            "kind": "question",
            "questions": [
                {
                    "id": "q1",
                    "text": "Continue?",
                    "multiple": False,
                    "options": [{"label": credential, "description": credential}],
                }
            ],
        },
    )

    agent_messages.signal_gate_requests(task, [event])

    message = AgentMessage.objects.get()
    assert message.options == [{"id": "o0", "label": "[redacted]", "description": "[redacted]"}]
    response = _send(task, key, kind="question", body="?", options=[{"id": credential, "label": "Pick"}])
    assert response.status_code == 400
    assert AgentMessage.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_runs_share_one_agent_rate_allowance(world, settings):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections

    settings.AGENT_MESSAGE_RATE_PER_MINUTE = 1
    runs = [_task(world) for _ in range(4)]
    ready = Barrier(len(runs))

    def send(run):
        close_old_connections()
        try:
            ready.wait(timeout=10)
            task, key = run
            return _send(task, key, body="update").status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(runs)) as pool:
        statuses = sorted(pool.map(send, runs))

    assert statuses == [201, 429, 429, 429]
    assert AgentMessage.objects.count() == 1


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


def _poll(world, user, after):
    tenant = TenantContext(organization_id=world.org.pk, actor_user_id=user.pk)
    return _new_for_identity(tenant, None, RequestAttributes(actor_user_id=user.pk), after)


def test_the_subscription_delivers_each_message_once_per_client(world):
    task, key = _task(world)
    _send(task, key, body="before")
    _, client_a = _poll(world, world.user, None)
    _, client_b = _poll(world, world.user, None)

    _send(task, key, body="after")
    items_a, client_a = _poll(world, world.user, client_a)
    items_b, client_b = _poll(world, world.user, client_b)
    again_a, _ = _poll(world, world.user, client_a)

    assert [m.body for m in items_a] == ["after"]
    assert [m.body for m in items_b] == ["after"]
    assert again_a == []


def test_the_subscription_stops_for_someone_who_left_the_org(world):
    task, key = _task(world)
    _, watermark = _poll(world, world.user, None)
    Member.objects.filter(user=world.user).update(is_active=False)
    _send(task, key, body="x")

    items, _ = _poll(world, world.user, watermark)

    assert items is None


# ---------------------------------------------------------------------------
# Signals and retention
# ---------------------------------------------------------------------------


def test_a_finished_run_reaches_its_launcher_once(world, django_capture_on_commit_callbacks):
    task, _ = _task(world)
    AgentTask.objects.filter(pk=task.pk).update(result={"output": "3 PRs merged"})
    task.refresh_from_db()

    with django_capture_on_commit_callbacks(execute=True):
        task.transition_to(AgentTask.Status.COMPLETED)
    agent_messages.signal_task_finished(task, AgentTask.Status.COMPLETED)

    [message] = AgentMessage.objects.all()
    assert (message.kind, message.source, message.body) == ("result", "platform", "3 PRs merged")
    assert AgentMessageRecipient.objects.get().user_id == world.user.pk


def test_retention_follows_the_orgs_audit_window(world):
    task, key = _task(world)
    message_id = _send(task, key, body="old").json()["id"]
    world.org.audit_log_retention_days = 30
    world.org.save(update_fields=["audit_log_retention_days"])
    AgentMessage.objects.filter(guid=message_id).update(
        created_at=timezone.now() - timezone.timedelta(days=31)
    )
    _send(task, key, body="new")

    assert agent_messages.purge_expired_messages() == 1
    assert list(AgentMessage.objects.values_list("body", flat=True)) == ["new"]
