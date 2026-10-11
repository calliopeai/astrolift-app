"""MCP replay uses native dispatch and requester/permission-scoped recovery."""

from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.utils import timezone
from temporalio.client import Client

from astrolift_agents.models import AgentTask
from astrolift_agents.tests.test_mcp_gateway import _agent, _call, _token
from astrolift_identity.api_tokens import SCOPE_MCP_DISPATCH, SCOPE_MCP_READ
from astrolift_identity.models import ApiToken, Member, Organization, Team
from core.permissions import Permission, PermissionScope, ScopeKind

pytestmark = pytest.mark.django_db


def invoke(org, user, token, name, **arguments):
    _, body = _call(org, user, token, "tools/call", {"name": name, "arguments": arguments})
    return body["result"]


def recover(org, user, token, key):
    return invoke(org, user, token, "astrolift_get_task_by_client_request_id", client_request_id=str(key))


def test_replay_and_recovery_preserve_one_real_temporal_execution(permission_resolver, settings):
    from astrolift_workflows import client as workflow_client

    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    settings.TEMPORAL_TASK_QUEUE = f"mcp-recovery-{uuid4()}"
    org = Organization.objects.create(name="Recovery", slug="recovery")
    user, token = _token(org, scopes=[SCOPE_MCP_READ, SCOPE_MCP_DISPATCH])
    workload = _agent(org)
    key = str(uuid4())
    args = {"agent_slug": workload.slug, "client_request_id": key, "trigger_payload": {"goal": "triage"}}
    workflow_client._client = None

    @async_to_sync
    async def inspect(task_id, *, cleanup=False):
        client = await Client.connect(settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE)
        handle = client.get_workflow_handle(f"DispatchAgentTaskWorkflow-{task_id}")
        description = await handle.describe()
        history = await handle.fetch_history()
        if cleanup:
            await handle.terminate("MCP recovery test complete")
        return description, history

    task_id = None
    try:
        first = invoke(org, user, token, "astrolift_run_agent", **args)
        assert first["isError"] is False, first
        task_id = first["structuredContent"]["task_id"]
        original, history = inspect(task_id)
        assert original.workflow_type == "DispatchAgentTaskWorkflow"
        assert sum(e.HasField("workflow_execution_started_event_attributes") for e in history.events) == 1

        again = invoke(org, user, token, "astrolift_run_agent", **args)
        assert again == first
        assert recover(org, user, token, key)["structuredContent"]["task"]["id"] == task_id
        after, _ = inspect(task_id)
        assert after.run_id == original.run_id
        assert AgentTask.objects.filter(organization=org, created_by=user, client_request_id=key).count() == 1

        conflict = invoke(
            org, user, token, "astrolift_run_agent", **{**args, "trigger_payload": {"goal": "changed"}}
        )
        assert conflict["isError"] is True
        assert conflict["structuredContent"]["code"] == "precondition"
        assert inspect(task_id)[0].run_id == original.run_id
    finally:
        if task_id is not None:
            inspect(task_id, cleanup=True)
        workflow_client._client = None


def test_recovery_is_requester_org_and_live_task_scoped(permission_resolver):
    permission_resolver.grant(Permission.AGENT_READ)
    org = Organization.objects.create(name="Recovery", slug="recovery")
    user, token = _token(org, scopes=[SCOPE_MCP_READ])
    other_user = get_user_model().objects.create_user(username="other-requester")
    Member.objects.create(user=other_user, scope_kind=Member.ScopeKind.ORG, scope_id=org.pk)
    key = uuid4()
    task = AgentTask.objects.create(organization=org, created_by=other_user, client_request_id=key)
    assert recover(org, user, token, key)["structuredContent"] == {"task": None}

    task.created_by = user
    task.save(update_fields=["created_by"])
    assert recover(org, user, token, key)["structuredContent"]["task"]["id"] == str(task.guid)
    renewed = ApiToken.objects.create(
        organization=org,
        user=user,
        name="renewed",
        token_hash="renewed",
        token_last_4="test",
        scopes=[SCOPE_MCP_READ],
    )
    assert recover(org, user, renewed, key)["structuredContent"]["task"]["id"] == str(task.guid)
    assert recover(org, user, token, uuid4())["structuredContent"] == {"task": None}

    other_org = Organization.objects.create(name="Other", slug="other")
    Member.objects.create(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=other_org.pk)
    other_token = ApiToken.objects.create(
        organization=other_org,
        user=user,
        name="other",
        token_hash="other",
        token_last_4="test",
        scopes=[SCOPE_MCP_READ],
    )
    assert recover(other_org, user, other_token, key)["structuredContent"] == {"task": None}
    task.deleted_at = timezone.now()
    task.save(update_fields=["deleted_at"])
    assert recover(org, user, token, key)["structuredContent"] == {"task": None}


def test_recovery_requires_visible_owner_and_token_team_ceiling(permission_resolver):
    org = Organization.objects.create(name="Recovery", slug="recovery")
    allowed = Team.objects.create(organization=org, name="Allowed", slug="allowed")
    hidden = Team.objects.create(organization=org, name="Hidden", slug="hidden")
    user, token = _token(org, scopes=[SCOPE_MCP_READ], team=allowed)
    task = AgentTask.objects.create(organization=org, created_by=user, team=hidden, client_request_id=uuid4())
    permission_resolver.grant(Permission.AGENT_READ)
    assert recover(org, user, token, task.client_request_id)["structuredContent"] == {"task": None}
    token.team = None
    token.save(update_fields=["team"])
    permission_resolver.deny(Permission.AGENT_READ)
    permission_resolver.grant(
        Permission.AGENT_READ, scope=PermissionScope(kind=ScopeKind.TEAM, id=allowed.pk)
    )
    assert recover(org, user, token, task.client_request_id)["structuredContent"] == {"task": None}
    task.team = allowed
    task.save(update_fields=["team"])
    assert recover(org, user, token, task.client_request_id)["structuredContent"]["task"]["id"] == str(
        task.guid
    )


@pytest.mark.parametrize("scope,grant", [(SCOPE_MCP_DISPATCH, True), (SCOPE_MCP_READ, False)])
def test_recovery_requires_read_scope_and_permission(permission_resolver, scope, grant):
    org = Organization.objects.create(name="Recovery", slug="recovery")
    user, token = _token(org, scopes=[scope])
    if grant:
        permission_resolver.grant(Permission.AGENT_READ)
    result = recover(org, user, token, uuid4())
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "permission_denied"


@pytest.mark.parametrize("key", ["bad", "", 17, None])
def test_invalid_request_ids_cannot_dispatch_or_recover(permission_resolver, key):
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    org = Organization.objects.create(name="Recovery", slug="recovery")
    user, token = _token(org, scopes=[SCOPE_MCP_READ, SCOPE_MCP_DISPATCH])
    workload = _agent(org)
    for name, args in (
        ("astrolift_run_agent", {"agent_slug": workload.slug}),
        ("astrolift_get_task_by_client_request_id", {}),
    ):
        result = invoke(org, user, token, name, **args, client_request_id=key)
        assert result["isError"] is True
        assert result["structuredContent"]["code"] in {"validation", "invalid_arguments"}
    assert not AgentTask.objects.exists()
