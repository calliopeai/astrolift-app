"""Durable replay and group authority against PostgreSQL, without ORM mocks."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections

from astrolift_agents.models import (
    AgentHostAction,
    AgentHostAuthority,
    AgentTask,
    AgentTaskEvent,
    AgentTaskInputMessage,
    AgentTaskInputReply,
)
from astrolift_agents.services.agent_host import AgentHost, HostError
from astrolift_agents.services.agent_host_projection import ROOT, chat_uri, session_uri, snapshots
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, OrganizationModule
from astrolift_operations.models import AuditEvent
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world():
    world = ScopeWorld("agent-host")
    world.user = make_user("agent-host")
    world.other = make_user("agent-host-other")
    bind_role(
        world.user,
        permissions=[Permission.AGENT_READ],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug="host-viewer",
    )
    bind_role(
        world.other,
        permissions=[Permission.AGENT_READ, Permission.AGENT_TASK_SEND_INPUT],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug="host-controller",
    )
    OrganizationModule.objects.create(organization=world.org, key="agent_live_attach", enabled=True)
    world.task = AgentTask.objects.create(organization=world.org, team=world.medops, status="running")
    world.hidden = AgentTask.objects.create(organization=world.org, team=world.platform, status="running")
    return world


def tenant(world, user=None):
    return TenantContext(organization_id=world.org.pk, actor_user_id=(user or world.user).pk)


def host(world, user=None, client="test-client", channels=None):
    user = user or world.user
    h = AgentHost(user, tenant(world, user))
    h.command(
        "initialize",
        {
            "channel": ROOT,
            "clientId": client,
            "protocolVersions": ["1.0.0"],
            "initialSubscriptions": channels if channels is not None else [chat_uri(world.task)],
        },
    )
    return h


def add_event(task, text="Hello", kind="assistant_delta", request=None, message="message-1"):
    task.refresh_from_db()
    task.event_sequence += 1
    row = AgentTaskEvent.objects.create(
        organization=task.organization,
        agent_task=task,
        sequence=task.event_sequence,
        turn_id="turn-1",
        message_id=message,
        kind=kind,
        text=text,
        request=request,
    )
    task.save()
    return row


def steer(task, seq=1, text="Review the brief"):
    return {
        "channel": chat_uri(task),
        "clientSeq": seq,
        "action": {
            "type": "chat/pendingMessageSet",
            "kind": "steering",
            "id": "c934b9d8-2d29-4466-8c74-52ff605d896d",
            "message": {"text": text, "origin": {"kind": "user"}},
        },
    }


def test_version_negotiation_and_initialize_first(world):
    with tenant_context(tenant(world)):
        h = AgentHost(world.user, tenant(world))
        with pytest.raises(HostError, match="first message"):
            h.command("listSessions", {"channel": ROOT})
        with pytest.raises(HostError) as error:
            h.command("initialize", {"channel": ROOT, "clientId": "c", "protocolVersions": ["0.9.0"]})
        assert error.value.code == -32005


def test_catalog_and_subscriptions_obey_group_read_scope(world):
    with tenant_context(tenant(world)):
        h = host(world)
        assert [i["resource"] for i in h.command("listSessions", {"channel": ROOT})["items"]] == [
            session_uri(world.task)
        ]
        with pytest.raises(HostError, match="permission denied"):
            h.command("subscribe", {"channel": chat_uri(world.hidden)})


def test_replay_survives_a_new_worker_without_duplicate_projection(world):
    with tenant_context(tenant(world)):
        h = host(world)
        initial = h.server_seq
        add_event(world.task)
        actions = h.poll()
        assert [a["action"]["type"] for a in actions] == [
            "chat/turnStarted",
            "chat/responsePart",
            "chat/delta",
        ]
        assert not h.poll()
        replacement = host(world, client="test-client", channels=[])
        replay = replacement.command(
            "reconnect",
            {
                "channel": ROOT,
                "clientId": "test-client",
                "lastSeenServerSeq": initial,
                "subscriptions": [chat_uri(world.task)],
            },
        )
        assert replay == {"type": "replay", "actions": actions, "missing": []}
        assert AgentHostAction.objects.count() == 5


def test_subscribing_to_another_channel_does_not_skip_existing_updates(world):
    with tenant_context(tenant(world)):
        h = host(world)
        add_event(world.task)
        snap = h.command("subscribe", {"channel": session_uri(world.task)})["snapshot"]
        actions = h.poll()
        assert [a["action"]["type"] for a in actions] == [
            "chat/turnStarted",
            "chat/responsePart",
            "chat/delta",
        ]
        assert all(a["serverSeq"] <= snap["fromSeq"] for a in actions)


def test_viewer_cannot_steer_and_rejection_is_audited(world):
    with tenant_context(tenant(world)):
        h = host(world)
        reply = h.command("dispatchAction", steer(world.task))
        assert "permission denied" in reply["rejectionReason"]
        assert not AgentTaskInputMessage.objects.exists()
        assert (
            AuditEvent.objects.filter(
                action="agent_host.dispatch", decision="DENY", organization=world.org
            ).count()
            == 1
        )


def test_controller_retries_are_idempotent_and_cross_actor_client_ids_are_isolated(world):
    with tenant_context(tenant(world, world.other)):
        h = host(world, world.other)
        action = steer(world.task)
        result = h.command("dispatchAction", action)
        assert "rejectionReason" not in result
        assert h.command("dispatchAction", action) == result
        assert AgentTaskInputMessage.objects.count() == 1
        with pytest.raises(HostError, match="another action"):
            h.command("dispatchAction", steer(world.task, text="Different body"))
    with tenant_context(tenant(world)):
        viewer = host(world)
        rejected = viewer.command("dispatchAction", action)
        assert rejected["serverSeq"] > result["serverSeq"]
        assert "rejectionReason" in rejected


def test_permission_revocation_stops_replay_and_reconnect_drops_channel(world):
    from astrolift_identity.models import RoleBinding

    with tenant_context(tenant(world)):
        h = host(world)
        last = h.server_seq
        RoleBinding.objects.filter(user=world.user).update(deleted_at=world.task.created_at)
        add_event(world.task)
        with pytest.raises(HostError):
            h.poll()
        replay = h.command(
            "reconnect",
            {"clientId": "test-client", "lastSeenServerSeq": last, "subscriptions": [chat_uri(world.task)]},
        )
        assert replay["missing"] == [chat_uri(world.task)] and not replay["actions"]


def test_token_ceiling_caps_user_controller_grant(world):
    token = ApiToken.objects.create(
        user=world.other, organization=world.org, name="Read only", token_hash="f" * 64, scopes=["read:apps"]
    )
    marker = set_current_api_token(token)
    try:
        with tenant_context(tenant(world, world.other)):
            h = host(world, world.other)
            assert "rejectionReason" in h.command("dispatchAction", steer(world.task))
            assert not AgentTaskInputMessage.objects.exists()
    finally:
        reset_current_api_token(marker)


def test_concurrent_projection_workers_produce_one_ordered_stream(world):
    add_event(world.task)

    def read():
        close_old_connections()
        try:
            return snapshots([AgentTask.objects.get(pk=world.task.pk)])[0]
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: read(), range(2)))
    assert results[0] == results[1] == AgentHostAuthority.objects.get(organization=world.org).server_sequence
    assert list(AgentHostAction.objects.values_list("server_sequence", flat=True)) == [1, 2, 3, 4]


def test_approval_is_not_an_arbitrary_tool_input_override(world):
    event = add_event(
        world.task,
        kind="approval_required",
        message="tool-1",
        request={"version": 1, "kind": "approval", "tool": {"name": "Bash", "input": {"command": "pwd"}}},
    )
    with tenant_context(tenant(world, world.other)):
        h = host(world, world.other)
        action = {
            "type": "chat/toolCallConfirmed",
            "turnId": event.turn_id,
            "toolCallId": f"request-{event.sequence}",
            "approved": True,
            "confirmed": "user-action",
            "editedToolInput": "dangerous",
        }
        rejected = h.command(
            "dispatchAction", {"channel": chat_uri(world.task), "clientSeq": 1, "action": action}
        )
        assert "editing registered tool input" in rejected["rejectionReason"]
        assert not AgentTaskInputReply.objects.exists()
        del action["editedToolInput"]
        accepted = h.command(
            "dispatchAction", {"channel": chat_uri(world.task), "clientSeq": 2, "action": action}
        )
        assert "rejectionReason" not in accepted
        assert AgentTaskInputReply.objects.get().response == {"decision": "allow"}


def test_organization_module_kill_switch_preserves_fallback_reason(world):
    from constance.test import override_config

    with tenant_context(tenant(world)), override_config(AGENT_LIVE_ATTACH_ALLOWED=False):
        h = host(world, channels=[ROOT])
        with pytest.raises(HostError) as error:
            h.command("subscribe", {"channel": chat_uri(world.task)})
        assert error.value.data == {
            "ahp_available": False,
            "reason": "module_disabled_by_install",
            "fallback": "agent_task.watch",
        }


def test_root_notifications_refresh_only_authorized_summaries(world):
    with tenant_context(tenant(world)):
        h = host(world, channels=[ROOT])
        first = h.catalog_notifications()
        assert len(first) == 1
        assert first[0]["method"] == "root/sessionAdded"
        assert first[0]["params"]["summary"]["resource"] == session_uri(world.task)
        assert h.catalog_notifications() == []
        world.task.status = "completed"
        world.task.save()
        changed = h.catalog_notifications()
        assert changed[0]["method"] == "root/sessionSummaryChanged"
        assert changed[0]["params"]["changes"]["status"] == 1
        assert not {"resource", "provider", "createdAt"} & changed[0]["params"]["changes"].keys()
        from astrolift_identity.models import RoleBinding

        RoleBinding.objects.filter(user=world.user).update(deleted_at=world.task.created_at)
        removed = h.catalog_notifications()
        assert removed == [
            {"method": "root/sessionRemoved", "params": {"channel": ROOT, "session": session_uri(world.task)}}
        ]
