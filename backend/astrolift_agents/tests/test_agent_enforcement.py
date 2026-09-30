"""Signed install actions, actual controls and scoped quarantine recovery on PG."""

import copy
import json
import uuid
from types import SimpleNamespace

import pytest
from django.test import RequestFactory
from django.utils import timezone

from astrolift_agents.models import (
    AgentDispatchQuarantine,
    AgentEnforcementAction,
    AgentEnforcementNonce,
    AgentEnvironmentSpec,
    AgentHostAction,
    AgentTask,
    AgentTaskInputMessage,
    AgentTaskInputReply,
)
from astrolift_agents.schema.mutations import AgentsMutation
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.services import agent_enforcement as policy
from astrolift_agents.services.agent_host_projection import chat_uri, snapshots
from astrolift_agents.tests import test_agent_host as host_tests
from astrolift_identity.models import Member, OrganizationModule
from astrolift_operations.agent_enforcement_views import agent_enforcement
from astrolift_operations.models import (
    AuditEvent,
    Notification,
    ZentinelleClusterGateway,
    ZentinelleConnection,
)
from astrolift_registry.models import Workload
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import tenant_context
from core.tests.utils.scope_world import bind_role, make_cluster, make_info

pytestmark = pytest.mark.django_db(transaction=True)
world = host_tests.world
CREDENTIAL = "sk_astroinst_" + "a" * 48


@pytest.fixture
def governed(world):
    secret = encrypt_at_rest(CREDENTIAL.encode())
    world.cluster = make_cluster(world, "policy")
    world.connection = ZentinelleConnection.objects.create(
        organization=world.org,
        base_url="https://zentinelle.invalid",
        tenant_ids=["tenant-a"],
        zentinelle_install_id=str(uuid.uuid4()),
        credential_backend_kind=secret.backend_kind,
        credential_ciphertext=secret.backend_ref,
    )
    ZentinelleClusterGateway.objects.create(
        connection=world.connection, cluster=world.cluster, zentinelle_cluster_id=str(world.cluster.guid)
    )
    world.spec = AgentEnvironmentSpec.objects.create(
        organization=world.org, name="Claude", slug="policy", agent_type="claude"
    )
    world.definition = Workload.objects.create(
        registered_app=world.medops_app, name="Reviewer", slug="policy", kind="agent"
    )
    task = world.task
    task.environment_spec, task.agent_definition = world.spec, world.definition
    task.model_gateway_connection, task.model_gateway_agent_id = world.connection, "task-policy"
    task.dispatch_target, task.triggered_by_user = {"cluster_guid": str(world.cluster.guid)}, world.user
    task.save()
    OrganizationModule.objects.create(organization=world.org, key="agent_policy_enforcement", enabled=True)
    bind_role(
        world.other,
        permissions=[Permission.AGENT_READ, Permission.AGENT_DISPATCH],
        kind="ORG",
        scope_id=world.org.pk,
        slug="policy-owner",
    )
    return world


def body(world, **changes):
    value = {
        "version": 1,
        "action_id": str(uuid.uuid4()),
        "nonce": str(uuid.uuid4()),
        "expires_at": int(timezone.now().timestamp()) + 60,
        "install_id": world.connection.zentinelle_install_id,
        "tenant_id": "tenant-a",
        "target_kind": "task",
        "target_id": str(world.task.guid),
        "agent_id": world.task.model_gateway_agent_id,
        "cluster_id": str(world.cluster.guid),
        "action": "log",
        "block_level": None,
        "mode": "enforce",
        "policy_id": "policy-1",
        "policy_name": "No shell",
        "reason": "Unapproved shell",
        "message": "Use the registered tools",
        "evidence_url": "/api/zentinelle/v1/audit/evidence-1",
        "turn_id": "turn-1",
        "tool_call_id": "call-1",
    }
    value.update(changes)
    return value


def apply(world, value):
    return policy.apply_signed(world.connection, value, policy.signature(value, CREDENTIAL))


@pytest.mark.parametrize(
    "change",
    [
        "signature",
        "expired",
        "future",
        "nonce",
        "tenant",
        "install",
        "cluster",
        "agent",
        "target",
        "bool-version",
    ],
)
def test_bad_delivery_never_claims_or_controls_a_target(governed, change):
    value = body(governed)
    if change == "expired":
        value["expires_at"] -= 61
    elif change == "future":
        value["expires_at"] += 120
    elif change in ("tenant", "agent"):
        value[change + "_id"] = "foreign"
    elif change in ("install", "cluster", "target"):
        value[change + "_id"] = str(uuid.uuid4())
    elif change == "nonce":
        value["nonce"] = "invalid"
    elif change == "bool-version":
        value["version"] = True
    signed = "sha256=é" if change == "signature" else policy.signature(value, CREDENTIAL)
    with pytest.raises(policy.AgentEnforcementError):
        policy.apply_signed(governed.connection, value, signed)
    assert not AgentEnforcementAction.objects.exists()
    assert not AgentEnforcementNonce.objects.exists()
    assert not AgentTaskInputMessage.objects.exists()


def test_stable_action_and_fresh_nonce_repair_receipts_without_repeating_controls(governed):
    value = body(governed, action="steer")
    assert apply(governed, value)["applied_action"] == "steer"
    with pytest.raises(policy.AgentEnforcementError, match="nonce"):
        apply(governed, value)
    value["nonce"] = str(uuid.uuid4())
    assert apply(governed, value)["applied_action"] == "steer"
    assert AgentTaskInputMessage.objects.count() == 1
    assert AgentHostAction.objects.filter(action__part__id__startswith="policy-").count() == 1
    value["nonce"], value["message"] = str(uuid.uuid4()), "Different intent"
    with pytest.raises(policy.AgentEnforcementError, match="conflicts"):
        apply(governed, value)
    assert AuditEvent.objects.filter(action="agents.policy.enforcement").count() == 1


@pytest.mark.parametrize("mode", ["org-off", "audit"])
def test_observe_only_ceiling_prevents_real_stop(governed, mode):
    if mode == "org-off":
        OrganizationModule.objects.filter(key="agent_policy_enforcement").update(enabled=False)
    outcome = apply(
        governed,
        body(governed, action="block", block_level="stop", mode="audit" if mode == "audit" else "enforce"),
    )
    governed.task.refresh_from_db()
    assert outcome["status"] == "observed" and governed.task.status == "running"


def test_stop_changes_task_status_and_policy_evidence_replays(governed):
    outcome = apply(governed, body(governed, action="block", block_level="stop"))
    governed.task.refresh_from_db()
    assert outcome["applied_action"] == "stop_task" and governed.task.status == "cancelled"
    with tenant_context(host_tests.tenant(governed)):
        _, states = snapshots([governed.task])
    serialized = json.dumps(states[chat_uri(governed.task)])
    assert "No shell" in serialized and "evidence-1" in serialized and "stop_task" in serialized
    assert not governed.task.callback_token_hash


def test_reported_tool_hold_is_denied_but_late_control_uses_confirmed_key_revocation(governed, monkeypatch):
    gate = host_tests.add_event(
        governed.task,
        kind="approval_required",
        message="call-1",
        request={"version": 1, "kind": "approval", "tool": {"name": "Bash", "input": {"command": "pwd"}}},
    )
    monkeypatch.setattr(
        "astrolift_agents.services.agent_host_policy._install_call",
        lambda *a: SimpleNamespace(ok=True, body={"decision": "deny"}),
    )
    outcome = apply(governed, body(governed, action="block", block_level="tool_call"))
    assert outcome["applied_action"] == "block_tool", outcome
    assert AgentTaskInputReply.objects.get(request_event=gate).response["decision"] == "deny"
    revoked = []
    monkeypatch.setattr(policy, "revoke_agent_key", lambda **kwargs: revoked.append(kwargs) or "revoked")
    outcome = apply(governed, body(governed, action="redact"))
    assert outcome["applied_action"] == "revoke_key" and "unavailable" in outcome["reason"]
    assert len(revoked) == 1


def test_unavailable_revocation_escalates_to_actual_stop(governed, monkeypatch):
    monkeypatch.setattr(policy, "revoke_agent_key", lambda **kwargs: "install_refused")
    outcome = apply(governed, body(governed, action="block", block_level="revoke_key"))
    assert outcome["status"] == "applied" and outcome["applied_action"] == "stop_task"
    assert "revocation was unavailable" in outcome["reason"]


@pytest.mark.parametrize("kind", ["agent", "spec"])
def test_quarantine_blocks_future_dispatch_until_authorized_clear(governed, kind):
    value = body(governed, action="block", block_level="quarantine", quarantine_target=kind)
    assert apply(governed, value)["applied_action"] == "quarantine_" + kind
    row = AgentDispatchQuarantine.objects.get()
    pending = AgentTask.objects.create(
        organization=governed.org, agent_definition=governed.definition, environment_spec=governed.spec
    )
    with pytest.raises(ValueError, match="quarantined"):
        pending.transition_to("queued")
    with tenant_context(host_tests.tenant(governed)):
        assert AgentsMutation().clear_agent_quarantine(make_info(governed.user), id=str(row.guid)).errors
    with tenant_context(host_tests.tenant(governed, governed.other)):
        assert len(AgentsQuery().agent_quarantines(make_info(governed.other))) == 1
        assert not AgentsMutation().clear_agent_quarantine(make_info(governed.other), id=str(row.guid)).errors
    pending.transition_to("queued")
    assert pending.status == "queued"


def test_alert_only_reaches_active_scoped_owner_and_controllers(governed):
    for user in (governed.user, governed.other):
        Member.objects.create(user=user, scope_kind="ORG", scope_id=governed.org.pk)
    apply(governed, body(governed, action="alert"))
    assert set(Notification.objects.values_list("user_id", flat=True)) == {
        governed.user.pk,
        governed.other.pk,
    }
    governed.task.team = governed.platform
    governed.task.agent_definition = None
    governed.task.save()
    apply(governed, body(governed, action="alert"))
    assert Notification.objects.filter(user=governed.user).count() == 1
    assert Notification.objects.filter(user=governed.other).count() == 2


def test_install_http_delivery_refuses_missing_auth_and_applies_valid_signature(governed):
    value = body(governed)
    factory = RequestFactory()
    request = factory.post(
        "/app/zentinelle/agent-enforcement/", data=json.dumps(value), content_type="application/json"
    )
    assert agent_enforcement(request).status_code == 401
    request = factory.post(
        "/app/zentinelle/agent-enforcement/",
        data=json.dumps(value),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + CREDENTIAL,
        HTTP_X_ZENTINELLE_SIGNATURE=policy.signature(value, CREDENTIAL),
    )
    assert agent_enforcement(request).status_code == 200


def test_poller_retries_receipt_without_repeating_external_action(governed, monkeypatch):
    value = body(governed, action="steer")
    calls = []

    def remote(connection, method, path, payload=None, **kwargs):
        calls.append((method, path, payload))
        if method == "GET":
            delivery = copy.deepcopy(value)
            delivery["nonce"] = str(uuid.uuid4())
            return SimpleNamespace(
                ok=True,
                body={"actions": [{"body": delivery, "signature": policy.signature(delivery, CREDENTIAL)}]},
            )
        return SimpleNamespace(ok=True, body={})

    monkeypatch.setattr(policy, "_install_call", remote)
    from django.core.cache import cache

    for _ in range(2):
        cache.delete(f"agent-policy-poll:{governed.task.guid}")
        policy.poll_enforcements(governed.task)
    assert AgentTaskInputMessage.objects.count() == 1
    assert len([call for call in calls if call[0] == "POST"]) == 2


def test_runner_evidence_is_atomic_scoped_and_not_duplicated_on_callback_retry(governed, monkeypatch):
    from django.db import transaction

    from astrolift_agents.models import AgentTaskEvent
    from astrolift_agents.services.agent_task_events import commit_task_events, prepare_task_events
    from astrolift_agents.tests.test_structured_agent_events import lifecycle
    from astrolift_operations.models import Event

    monkeypatch.setattr(
        policy, "_install_call", lambda *args, **kwargs: SimpleNamespace(ok=True, body={"actions": []})
    )
    events = lifecycle()
    events[2]["data"]["input"] = {"secret": "sk-sensitive-input"}
    events[3]["data"]["output"] = "private-result"
    with pytest.raises(ValueError, match="rollback"):
        with transaction.atomic():
            commit_task_events(governed.task, prepare_task_events(governed.task, events))
            raise ValueError("rollback")
    assert not AgentTaskEvent.objects.filter(agent_task=governed.task).exists()
    assert not Event.objects.filter(event_type="AUDIT.agent.session.event").exists()
    governed.task.refresh_from_db()
    with transaction.atomic():
        commit_task_events(governed.task, prepare_task_events(governed.task, events))
    governed.task.refresh_from_db()
    with transaction.atomic():
        commit_task_events(governed.task, prepare_task_events(governed.task, events))
    evidence = list(Event.objects.filter(event_type="AUDIT.agent.session.event").order_by("id"))
    assert len(evidence) == 6
    assert all(
        row.organization_id == governed.org.pk and row.team_id == governed.medops.pk for row in evidence
    )
    input_row = next(row for row in evidence if row.payload["payload"]["kind"] == "tool_call_input")
    payload = input_row.payload["payload"]
    assert payload["tool_name"] == "Read" and payload["data"]["input_keys"] == ["secret"]
    assert payload["declared_intent"]["agent_id"] == str(governed.definition.guid)
    assert "sk-sensitive-input" not in json.dumps([row.payload for row in evidence])
    assert "private-result" not in json.dumps([row.payload for row in evidence])


def test_sibling_team_controller_cannot_list_or_clear_quarantine(governed):
    apply(governed, body(governed, action="block", block_level="quarantine"))
    row = AgentDispatchQuarantine.objects.get()
    from core.tests.utils.scope_world import make_user

    sibling = make_user("policy-sibling")
    bind_role(
        sibling,
        permissions=[Permission.AGENT_DISPATCH],
        kind="TEAM",
        scope_id=governed.platform.pk,
        slug="sibling-policy",
    )
    with tenant_context(host_tests.tenant(governed, sibling)):
        assert AgentsQuery().agent_quarantines(make_info(sibling)) == []
        assert AgentsMutation().clear_agent_quarantine(make_info(sibling), id=str(row.guid)).errors
    assert AgentDispatchQuarantine.objects.get().cleared_at is None


def test_box_stop_deletes_the_real_control_objects_and_journals_its_outcome(governed, monkeypatch):
    from astrolift_agents.models import AgentBox, AgentHostAuthority, AgentHostTerminal
    from astrolift_operations import zentinelle_connect
    from astrolift_operations.zentinelle_connect import _Reply

    governed.cluster.lifecycle = "managed"
    governed.cluster.save()
    deleted = []

    class Driver:
        def delete_manifests(self, *args, **kwargs):
            deleted.append((args, kwargs))
            return SimpleNamespace(ok=True)

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: Driver())
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda cluster: SimpleNamespace(slug=cluster.slug)
    )
    monkeypatch.setattr(zentinelle_connect, "_install_call", lambda *args, **kwargs: _Reply(200, {}))
    box = AgentBox.objects.create(
        organization=governed.org,
        owner=governed.other,
        name="Governed box",
        slug="governed-box",
        status="running",
        environment_spec=governed.spec,
        external_id="job-policy",
        namespace="agent-boxes",
        model_gateway_connection=governed.connection,
        model_gateway_agent_id="box-policy",
    )
    authority = AgentHostAuthority.objects.create(organization=governed.org)
    view = AgentHostTerminal.objects.create(
        authority=authority,
        agent_box=box,
        actor=governed.other,
        client_id="box-reader",
        channel="ahp-terminal:/policy-box",
        state={"content": []},
    )
    value = body(
        governed,
        target_kind="box",
        target_id=str(box.guid),
        agent_id="box-policy",
        action="block",
        block_level="stop",
    )
    assert apply(governed, value)["applied_action"] == "stop_box"
    box.refresh_from_db()
    view.refresh_from_db()
    assert box.status == "stopped"
    assert {ref["kind"] for ref in deleted[0][0][2]} == {"Job", "Secret"}
    assert deleted[0][1]["propagation_policy"] == "Foreground"
    assert "stop_box" in view.state["content"][0]["value"]
    value["nonce"] = str(uuid.uuid4())
    assert apply(governed, value)["applied_action"] == "stop_box"
    assert len(deleted) == 1 and AgentHostAction.objects.filter(agent_box=box).count() == 1


@pytest.mark.parametrize("token_team", [False, True])
def test_owned_spec_quarantines_follow_team_roles_and_token_ceiling(governed, token_team):
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token

    governed.spec.team = governed.medops
    governed.spec.save()
    apply(governed, body(governed, action="block", block_level="quarantine", quarantine_target="spec"))
    row = AgentDispatchQuarantine.objects.get()
    sibling = AgentEnvironmentSpec.objects.create(
        organization=governed.org, team=governed.platform, name="Sibling", slug="sibling-policy"
    )
    AgentDispatchQuarantine.objects.create(
        organization=governed.org,
        target_kind="spec",
        target_guid=sibling.guid,
    )
    user = governed.other if token_team else governed.user
    if not token_team:
        bind_role(
            user,
            permissions=[Permission.AGENT_DISPATCH],
            kind="TEAM",
            scope_id=governed.medops.pk,
            slug="team-policy-owner",
        )
    marker = None
    if token_team:
        marker = set_current_api_token(
            SimpleNamespace(
                organization_id=governed.org.pk,
                team_id=governed.medops.pk,
                scopes=["admin"],
            )
        )
    try:
        with tenant_context(host_tests.tenant(governed, user)):
            assert [str(item.id) for item in AgentsQuery().agent_quarantines(make_info(user))] == [
                str(row.guid)
            ]
            assert not AgentsMutation().clear_agent_quarantine(make_info(user), id=str(row.guid)).errors
    finally:
        if marker is not None:
            reset_current_api_token(marker)
    row.refresh_from_db()
    assert row.cleared_at is not None


def test_org_shared_quarantine_requires_org_scoped_credential(governed):
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token

    apply(governed, body(governed, action="block", block_level="quarantine", quarantine_target="spec"))
    row = AgentDispatchQuarantine.objects.get()
    marker = set_current_api_token(
        SimpleNamespace(
            organization_id=governed.org.pk,
            team_id=governed.medops.pk,
            scopes=["admin"],
        )
    )
    try:
        with tenant_context(host_tests.tenant(governed, governed.other)):
            assert len(AgentsQuery().agent_quarantines(make_info(governed.other))) == 1
            assert AgentsMutation().clear_agent_quarantine(make_info(governed.other), id=str(row.guid)).errors
    finally:
        reset_current_api_token(marker)
    row.refresh_from_db()
    assert row.cleared_at is None
