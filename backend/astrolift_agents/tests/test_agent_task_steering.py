"""Tests for the agent steering channel (#1390).

Covers the whole queued-follow-up path:

* **The mutation** ``sendAgentTaskInput`` — tenancy (fail-closed, cross-org
  reads as NOT_FOUND and writes nothing), deny-by-default permission,
  validation, the accepting-status precondition, and the audit row.
* **The queue** — FIFO ordering across a burst, and at-most-once delivery
  (a second consume never re-hands a claimed message).
* **The callback response** — the three ``input_intent`` modes, that a plain
  heartbeat is untouched (no new keys, no delivery), that a peek never marks
  anything delivered, and that a terminal task consumes nothing.

Runs against real Postgres (pytest-django). The only mock is the audit
writer capture.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from django.test import Client

from astrolift_agents.models import (
    AgentInteraction,
    AgentTask,
    AgentTaskInputMessage,
    Brief,
    DispatcherInstance,
)
from astrolift_agents.models.agent_task_input import MAX_MESSAGE_CHARS
from astrolift_agents.schema.mutations import AgentsMutation
from astrolift_agents.services.agent_task_input import (
    AgentTaskInputError,
    claim_pending_input,
    queue_agent_task_input,
)
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

CALLBACK = "/api/dispatch/v1/agents/{}/callback/"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def org():
    return Organization.objects.create(name="SteerOrg", slug="steer-org")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="OtherOrg", slug="steer-other-org")


@pytest.fixture
def raw_key() -> str:
    return "s" * 64


@pytest.fixture
def dispatcher(org, raw_key):
    return DispatcherInstance.objects.create(
        organization=org,
        name="Steer Dispatcher",
        slug="steer-dispatcher",
        endpoint="https://dispatch.invalid/",
        api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        cloud=DispatcherInstance.Cloud.K8S_NATIVE,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
    )


@pytest.fixture
def brief(org):
    return Brief.objects.create(
        organization=org,
        content_hash="b" * 64,
        manifest_snapshot={"system_prompt": "do the thing", "tools": []},
        context={},
    )


def _make_task(org, dispatcher=None, brief=None, status=AgentTask.Status.RUNNING):
    task = AgentTask.objects.create(organization=org, dispatcher=dispatcher, brief=brief)
    if status != AgentTask.Status.DRAFT:
        AgentTask.all_objects.filter(pk=task.pk).update(status=status)
        task.refresh_from_db()
    return task


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _auth(raw_key: str) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


def _callback(task, raw_key, **body):
    return Client().post(
        CALLBACK.format(task.guid),
        data=json.dumps(body),
        content_type="application/json",
        **_auth(raw_key),
    )


# ---------------------------------------------------------------------------
# sendAgentTaskInput — tenancy
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_send_input_rejects_another_orgs_task_and_writes_nothing(permission_resolver, org, other_org):
    """The core isolation contract.

    ``@tenant_scoped`` only asserts a tenant context exists and
    ``TenantScopedManager`` is wired to zero models, so the resolver's own
    ``organization_id`` filter is the only thing standing between a caller
    and every other org's task (guids are globally unique). Seed a task in
    ``other_org`` that WOULD be found by an unscoped ``filter(guid=...)``,
    call as ``org``, and assert it reads as NOT_FOUND with no row written —
    indistinguishable from a guid that does not exist, so existence does
    not leak either.
    """
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    foreign_task = _make_task(other_org, status=AgentTask.Status.RUNNING)

    with _tenant(org):
        result = AgentsMutation().send_agent_task_input(
            _info(), task_id=str(foreign_task.guid), message="stop and summarize"
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "taskId"
    assert AgentTaskInputMessage.objects.count() == 0


@pytest.mark.django_db
def test_send_input_accepts_own_orgs_task(permission_resolver, org):
    """Same call shape as the cross-org test, in-org — proves that test is
    meaningful (the NOT_FOUND above is the org filter, not a broken path)."""
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    task = _make_task(org, status=AgentTask.Status.RUNNING)

    with _tenant(org):
        result = AgentsMutation().send_agent_task_input(
            _info(), task_id=str(task.guid), message="stop and summarize"
        )

    assert result.ok is True, result.errors
    assert result.data.message == "stop and summarize"
    assert result.data.delivered_at is None
    row = AgentTaskInputMessage.objects.get()
    assert row.agent_task_id == task.pk
    assert row.organization_id == org.id
    assert row.body == "stop and summarize"


@pytest.mark.django_db
def test_send_input_unknown_task_reads_the_same_as_a_foreign_one(permission_resolver, org):
    """A never-existed guid and a foreign guid must be indistinguishable."""
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    with _tenant(org):
        result = AgentsMutation().send_agent_task_input(
            _info(),
            task_id="00000000-0000-7000-8000-000000000000",
            message="hello",
        )
    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# sendAgentTaskInput — permission
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_send_input_is_deny_by_default(permission_resolver, org):
    """No grant -> PERMISSION_DENIED envelope (``@mutation_audit`` catches the
    raise and translates it), and nothing is queued."""
    task = _make_task(org, status=AgentTask.Status.RUNNING)

    with _tenant(org):
        result = AgentsMutation().send_agent_task_input(_info(), task_id=str(task.guid), message="nudge")

    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert AgentTaskInputMessage.objects.count() == 0


@pytest.mark.django_db
def test_send_input_is_not_covered_by_the_watch_or_dispatch_grants(permission_resolver, org):
    """Steering is a write into a live agent — holding the passive watch
    grant, or the grant to START a run, must not authorize it."""
    permission_resolver.grant(Permission.AGENT_TASK_WATCH)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    task = _make_task(org, status=AgentTask.Status.RUNNING)

    with _tenant(org):
        result = AgentsMutation().send_agent_task_input(_info(), task_id=str(task.guid), message="nudge")

    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_send_input_audits_action_and_target(permission_resolver, org):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    task = _make_task(org, status=AgentTask.Status.RUNNING)

    captured = []
    from core import mutations as core_mutations

    original = core_mutations._audit_writer
    core_mutations.register_audit_writer(lambda e: captured.append(e))
    try:
        with _tenant(org):
            AgentsMutation().send_agent_task_input(_info(), task_id=str(task.guid), message="nudge")
    finally:
        core_mutations.register_audit_writer(original)

    rows = [e for e in captured if e.action == "agents.task.send_input"]
    assert len(rows) == 1
    assert rows[0].decision == "ALLOW"
    assert rows[0].target_kind == "AgentTask"
    assert rows[0].target_id == str(task.guid)


# ---------------------------------------------------------------------------
# sendAgentTaskInput — validation + precondition
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("message", ["", "   ", "\n\t "])
def test_send_input_rejects_a_blank_message(permission_resolver, org, message):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    task = _make_task(org, status=AgentTask.Status.RUNNING)

    with _tenant(org):
        result = AgentsMutation().send_agent_task_input(_info(), task_id=str(task.guid), message=message)

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "message"
    assert AgentTaskInputMessage.objects.count() == 0


@pytest.mark.django_db
def test_send_input_rejects_an_oversized_message(permission_resolver, org):
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    task = _make_task(org, status=AgentTask.Status.RUNNING)

    with _tenant(org):
        result = AgentsMutation().send_agent_task_input(
            _info(), task_id=str(task.guid), message="x" * (MAX_MESSAGE_CHARS + 1)
        )

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert AgentTaskInputMessage.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        AgentTask.Status.DRAFT,
        AgentTask.Status.QUEUED,
        AgentTask.Status.PROVISIONING,
        AgentTask.Status.COMPLETED,
        AgentTask.Status.CANCELLED,
    ],
)
def test_send_input_refuses_a_task_with_no_reachable_turn_boundary(permission_resolver, org, status):
    """A task that has not started has no turn boundary yet and a terminal
    one will never reach another, so queueing would strand the message.
    Refuse up front rather than accept-and-never-deliver."""
    permission_resolver.grant(Permission.AGENT_TASK_SEND_INPUT)
    task = _make_task(org, status=status)

    with _tenant(org):
        result = AgentsMutation().send_agent_task_input(_info(), task_id=str(task.guid), message="nudge")

    assert result.ok is False
    assert result.errors[0].code == "PRECONDITION"
    assert AgentTaskInputMessage.objects.count() == 0


# ---------------------------------------------------------------------------
# The queue — ordering and at-most-once
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_queue_service_refuses_a_non_running_task(org):
    task = _make_task(org, status=AgentTask.Status.QUEUED)
    with pytest.raises(AgentTaskInputError) as exc:
        queue_agent_task_input(task=task, message="nudge")
    assert exc.value.code == "precondition"


@pytest.mark.django_db
def test_claim_returns_messages_oldest_first(org):
    """FIFO across a burst written in the same transaction — ``created_at``
    can tie at that resolution, so the pk tiebreaker is what makes the
    order total. Written back-to-back on purpose to exercise that."""
    task = _make_task(org, status=AgentTask.Status.RUNNING)
    for n in range(5):
        queue_agent_task_input(task=task, message=f"msg-{n}")

    claimed = claim_pending_input(task)

    assert [row.body for row in claimed] == [f"msg-{n}" for n in range(5)]


@pytest.mark.django_db
def test_claim_is_at_most_once(org):
    """A second claim must not re-hand an already-delivered message."""
    task = _make_task(org, status=AgentTask.Status.RUNNING)
    queue_agent_task_input(task=task, message="first")
    queue_agent_task_input(task=task, message="second")

    first = claim_pending_input(task)
    second = claim_pending_input(task)

    assert [row.body for row in first] == ["first", "second"]
    assert second == []
    assert AgentTaskInputMessage.objects.filter(delivered_at__isnull=True).count() == 0


@pytest.mark.django_db
def test_claim_stamps_delivered_at_only_on_the_claimed_rows(org):
    task = _make_task(org, status=AgentTask.Status.RUNNING)
    other_task = _make_task(org, status=AgentTask.Status.RUNNING)
    mine = queue_agent_task_input(task=task, message="mine")
    theirs = queue_agent_task_input(task=other_task, message="theirs")

    claim_pending_input(task)

    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.delivered_at is not None
    assert theirs.delivered_at is None


@pytest.mark.django_db
def test_claim_respects_the_batch_cap_and_leaves_the_rest_queued(org):
    task = _make_task(org, status=AgentTask.Status.RUNNING)
    for n in range(4):
        queue_agent_task_input(task=task, message=f"msg-{n}")

    claimed = claim_pending_input(task, limit=2)

    assert [row.body for row in claimed] == ["msg-0", "msg-1"]
    assert AgentTaskInputMessage.objects.filter(delivered_at__isnull=True).count() == 2


# ---------------------------------------------------------------------------
# The state-callback response
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_plain_heartbeat_carries_no_input_keys_and_delivers_nothing(org, dispatcher, brief, raw_key):
    """The load-bearing separation: the periodic heartbeat is mid-turn, not a
    turn boundary. It must never consume a message, or a nudge would be
    marked delivered into a turn that cannot apply it."""
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    queue_agent_task_input(task=task, message="nudge")

    resp = _callback(task, raw_key, status="running", partial="...")

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body == {"ok": True, "continue": True}
    assert AgentTaskInputMessage.objects.filter(delivered_at__isnull=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_peek_reports_the_count_without_delivering(org, dispatcher, brief, raw_key):
    """What a runner whose harness cannot take a follow-up prompt sends: it
    learns there is queued input to log about, and the message stays
    visibly queued rather than being silently swallowed."""
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    queue_agent_task_input(task=task, message="one")
    queue_agent_task_input(task=task, message="two")

    resp = _callback(task, raw_key, status="running", input_intent="peek")

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["pending_input_count"] == 2
    assert "pending_input" not in body
    assert AgentTaskInputMessage.objects.filter(delivered_at__isnull=True).count() == 2


@pytest.mark.django_db(transaction=True)
def test_consume_returns_ordered_messages_and_marks_them_delivered(org, dispatcher, brief, raw_key):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    queue_agent_task_input(task=task, message="first", author_label="ada")
    queue_agent_task_input(task=task, message="second", author_label="grace")

    resp = _callback(task, raw_key, status="running", input_intent="consume")

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert [m["message"] for m in body["pending_input"]] == ["first", "second"]
    assert [m["author"] for m in body["pending_input"]] == ["ada", "grace"]
    assert all(m["id"] and m["created_at"] for m in body["pending_input"])
    # Nothing left behind after the claim.
    assert body["pending_input_count"] == 0
    assert body["continue"] is True
    assert AgentTaskInputMessage.objects.filter(delivered_at__isnull=True).count() == 0


@pytest.mark.django_db(transaction=True)
def test_consume_is_at_most_once_across_two_callbacks(org, dispatcher, brief, raw_key):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    queue_agent_task_input(task=task, message="only once")

    first = _callback(task, raw_key, status="running", input_intent="consume").json()
    second = _callback(task, raw_key, status="running", input_intent="consume").json()

    assert [m["message"] for m in first["pending_input"]] == ["only once"]
    assert second["pending_input"] == []


@pytest.mark.django_db(transaction=True)
def test_consume_records_a_signal_interaction(org, dispatcher, brief, raw_key):
    """Delivery is the genuine signal path into the pod, captured the way the
    cancel signal is — not just another control_api row."""
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    queued = queue_agent_task_input(task=task, message="nudge")

    _callback(task, raw_key, status="running", input_intent="consume")

    signals = list(AgentInteraction.objects.filter(agent_task=task, kind=AgentInteraction.Kind.SIGNAL))
    assert len(signals) == 1
    assert signals[0].name == "input"
    assert signals[0].detail["count"] == 1
    assert signals[0].detail["message_ids"] == [str(queued.guid)]
    assert signals[0].organization_id == org.id


@pytest.mark.django_db(transaction=True)
def test_consume_on_an_empty_queue_records_no_signal(org, dispatcher, brief, raw_key):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)

    body = _callback(task, raw_key, status="running", input_intent="consume").json()

    assert body["pending_input"] == []
    assert AgentInteraction.objects.filter(kind=AgentInteraction.Kind.SIGNAL).count() == 0


@pytest.mark.django_db(transaction=True)
def test_terminal_callback_consumes_nothing(org, dispatcher, brief, raw_key):
    """A completing pod has no next turn — do not mark a queued message
    delivered into it."""
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    queue_agent_task_input(task=task, message="too late")

    resp = _callback(task, raw_key, status="completed", result="all green", input_intent="consume")

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["continue"] is False
    assert body["pending_input"] == []
    assert body["pending_input_count"] == 0
    assert AgentTaskInputMessage.objects.filter(delivered_at__isnull=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_callback_rejects_an_unknown_input_intent(org, dispatcher, brief, raw_key):
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)

    resp = _callback(task, raw_key, status="running", input_intent="drain")

    assert resp.status_code == 400
    assert "input_intent" in resp.json()["error"]


@pytest.mark.django_db(transaction=True)
def test_callback_never_hands_input_across_tasks(org, dispatcher, brief, raw_key):
    """The claim is keyed on the authenticated task, not the org."""
    task = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    sibling = _make_task(org, dispatcher, brief, AgentTask.Status.RUNNING)
    queue_agent_task_input(task=sibling, message="for the sibling")

    body = _callback(task, raw_key, status="running", input_intent="consume").json()

    assert body["pending_input"] == []
    assert AgentTaskInputMessage.objects.filter(delivered_at__isnull=True).count() == 1
