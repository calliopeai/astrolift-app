"""Fleet discovery through the same scoped API used to dispatch agents."""

from __future__ import annotations

import pytest
from django.utils import timezone

from astrolift_agents.models import AgentTask, DispatcherInstance
from astrolift_agents.runtime_catalog import RUNTIME_NAMES
from astrolift_agents.tests.test_mcp_gateway import _agent, _call, _token
from astrolift_identity.api_tokens import SCOPE_MCP_DISPATCH, SCOPE_MCP_READ
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import AppTeamAccess
from core.permissions import Permission

pytestmark = pytest.mark.django_db


def _task(org, workload=None, *, status="running", **kwargs):
    task = AgentTask.objects.create(organization=org, agent_definition=workload, **kwargs)
    if status == "draft":
        return task
    for state in ("queued", "provisioning", "running", "completed"):
        task.transition_to(state)
        if state == status:
            break
    return task


def _read(org, user, token, name="astrolift_list_tasks", **arguments):
    _, payload = _call(org, user, token, "tools/call", {"name": name, "arguments": arguments})
    assert payload["result"]["isError"] is False, payload
    return payload["result"]["structuredContent"]


def test_task_discovery_reports_live_placement_and_filters(permission_resolver):
    org = Organization.objects.create(name="Fleet", slug="fleet")
    user, token = _token(org, scopes=[SCOPE_MCP_READ])
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    project = Project.objects.create(
        organization=org,
        team=agent.registered_app.team,
        name="Operations",
        slug="operations",
    )
    agent.registered_app.project = project
    agent.registered_app.save(update_fields=["project", "updated_at", "version"])
    dispatcher = DispatcherInstance.objects.create(
        organization=org,
        name="Local",
        slug="local",
        endpoint="https://dispatcher.invalid",
        backend="k8s_job",
        cloud="k8s_native",
        region="local",
        status="active",
        api_key_hash="must-not-be-returned",
        last_heartbeat_at=timezone.now(),
    )
    task = _task(org, agent, dispatcher=dispatcher, pod_name="agent-pod", namespace="fleet-agents")
    _task(org, agent, status="completed")
    _task(org, _agent(org, slug="other", team=agent.registered_app.team))

    page = _read(org, user, token, status="running", agent_slug="triage", project_slug="operations")
    assert [row["id"] for row in page["tasks"]] == [str(task.guid)]
    row = page["tasks"][0]
    assert row["pod_name"] == "agent-pod"
    assert row["namespace"] == "fleet-agents"
    assert row["dispatcher"]["id"] == str(dispatcher.guid)
    assert row["dispatcher"]["backend"] == "k8s_job"
    assert row["started_at"] and row["provisioning_at"]
    assert "api_key_hash" not in row["dispatcher"]
    assert "endpoint" not in row["dispatcher"]
    assert "result" not in row
    assert page["next_cursor"] is None
    assert _read(org, user, token, project_slug="missing")["tasks"] == []
    detail = _read(org, user, token, "astrolift_get_task", task_id=str(task.guid))
    assert detail["dispatcher"] == row["dispatcher"]
    assert "result" in detail


def test_task_pages_have_stable_tiebreaks_and_do_not_repeat_new_arrivals(permission_resolver):
    org = Organization.objects.create(name="Fleet", slug="pages")
    user, token = _token(org, scopes=[SCOPE_MCP_READ])
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    tasks = [_task(org, agent) for _ in range(5)]
    AgentTask.objects.filter(organization=org).update(created_at=timezone.now())
    first = _read(org, user, token, limit=2)
    assert len(first["tasks"]) == 2
    seen = [row["id"] for row in first["tasks"]]
    newer = _task(org, agent)
    cursor = first["next_cursor"]
    while cursor:
        page = _read(org, user, token, limit=2, cursor=cursor)
        seen.extend(row["id"] for row in page["tasks"])
        cursor = page["next_cursor"]
    assert len(seen) == len(set(seen)) == 5
    assert set(seen) == {str(task.guid) for task in tasks}
    assert str(newer.guid) not in seen


def test_task_discovery_enforces_org_team_and_live_agent_access(permission_resolver):
    org = Organization.objects.create(name="Fleet", slug="scoped-fleet")
    foreign = Organization.objects.create(name="Foreign", slug="foreign-fleet")
    team = Team.objects.create(organization=org, name="Reader", slug="reader")
    other = Team.objects.create(organization=org, name="Owner", slug="owner")
    user, token = _token(org, scopes=[SCOPE_MCP_READ], team=team)
    permission_resolver.grant(Permission.AGENT_READ)
    own = _task(org, _agent(org, slug="own", team=team))
    shared_agent = _agent(org, slug="shared", team=other)
    shared = _task(org, shared_agent)
    share = AppTeamAccess.objects.create(
        registered_app=shared_agent.registered_app,
        team=team,
        access_level=AppTeamAccess.AccessLevel.VIEWER,
    )
    hidden = _task(org, _agent(org, slug="hidden", team=other))
    _task(foreign, _agent(foreign))
    _task(org)
    deleted = _task(org, own.agent_definition)
    deleted.deleted_at = timezone.now()
    deleted.save(update_fields=["deleted_at", "updated_at", "version"])

    rows = _read(org, user, token)["tasks"]
    assert {row["id"] for row in rows} == {str(own.guid), str(shared.guid)}
    assert _read(org, user, token, agent_slug=hidden.agent_definition.slug)["tasks"] == []
    share.deleted_at = timezone.now()
    share.save(update_fields=["deleted_at", "updated_at", "version"])
    assert [row["id"] for row in _read(org, user, token)["tasks"]] == [str(own.guid)]
    own.agent_definition.deleted_at = timezone.now()
    own.agent_definition.save(update_fields=["deleted_at", "updated_at", "version"])
    assert _read(org, user, token)["tasks"] == []


@pytest.mark.parametrize("tool", ["astrolift_list_tasks", "astrolift_list_runtimes"])
@pytest.mark.parametrize("has_scope,has_permission", [(False, True), (True, False)])
def test_discovery_requires_scope_and_rbac(permission_resolver, tool, has_scope, has_permission):
    org = Organization.objects.create(name="Denied", slug="denied-fleet")
    user, token = _token(org, scopes=[SCOPE_MCP_READ] if has_scope else [SCOPE_MCP_DISPATCH])
    if has_permission:
        permission_resolver.grant(Permission.AGENT_READ)
    _, listed = _call(org, user, token, "tools/list")
    assert tool not in {row["name"] for row in listed["result"]["tools"]}
    _, result = _call(org, user, token, "tools/call", {"name": tool, "arguments": {}})
    assert result["result"]["isError"] is True


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 201}, {"limit": True}, {"status": "bogus"}])
def test_invalid_task_filters_are_rejected(permission_resolver, arguments):
    org = Organization.objects.create(name="Fleet", slug="invalid-fleet")
    user, token = _token(org, scopes=[SCOPE_MCP_READ])
    permission_resolver.grant(Permission.AGENT_READ)
    _, result = _call(
        org, user, token, "tools/call", {"name": "astrolift_list_tasks", "arguments": arguments}
    )
    assert result["result"]["isError"] is True
    assert result["result"]["structuredContent"]["code"] == "invalid_arguments"


def test_runtime_discovery_uses_the_dispatch_catalog(permission_resolver, monkeypatch):
    org = Organization.objects.create(name="Fleet", slug="runtime-fleet")
    user, token = _token(org, scopes=[SCOPE_MCP_READ])
    permission_resolver.grant(Permission.AGENT_READ)
    monkeypatch.setenv("ASTROLIFT_AGENT_REGISTRY", "registry.example/agents")
    monkeypatch.setenv("ASTROLIFT_AGENT_RUNTIME_TAG", "tested")
    rows = _read(org, user, token, "astrolift_list_runtimes")["runtimes"]
    assert {row["name"] for row in rows} == set(RUNTIME_NAMES)
    assert all(row["image"].startswith("registry.example/agents/") for row in rows)
    assert all(row["image"].endswith(":tested") for row in rows)
