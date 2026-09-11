"""MCP adapters must preserve the fleet/task boundary with real role grants."""

import hashlib
import json

import pytest
from django.test import Client
from django.utils import timezone

from astrolift_agents.models import AgentTask, Brief, DispatcherInstance
from astrolift_agents.tests.test_fleet_scopes_1745 import bind
from astrolift_agents.tests.test_fleet_scopes_1745 import fleet as fleet_fixture
from astrolift_agents.tests.test_fleet_scopes_1745 import no_opensearch as no_opensearch
from astrolift_agents.tests.test_mcp_gateway import _request
from astrolift_agents.views.mcp import mcp_gateway
from astrolift_identity.api_tokens import (
    SCOPE_MCP_DISPATCH,
    SCOPE_MCP_READ,
    SCOPE_READ_APPS,
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import ApiToken
from astrolift_registry.models import AppTeamAccess, Workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld

pytestmark = pytest.mark.django_db
fleet = fleet_fixture


@pytest.fixture
def mcp(fleet):
    fleet.plaintext = "alft_at_mcp-scopes-1747"
    fleet.credential = ApiToken.objects.create(
        user=fleet.user,
        organization=fleet.world.org,
        name="MCP scopes",
        token_hash=hashlib.sha256(fleet.plaintext.encode()).hexdigest(),
        token_last_4="1747",
        scopes=[SCOPE_MCP_READ, SCOPE_MCP_DISPATCH, SCOPE_READ_APPS],
    )
    return fleet


def rpc(mcp, method, params=None, *, selected=False):
    marker = set_current_api_token(mcp.credential)
    try:
        with tenant_context(
            TenantContext(
                organization_id=mcp.world.org.pk,
                actor_user_id=mcp.user.pk,
                team_id=mcp.world.medops.pk if selected else mcp.credential.team_id,
                project_id=mcp.world.medops_project.pk if selected else None,
            )
        ):
            response = mcp_gateway(_request(mcp.user, mcp.credential, method, params))
        return json.loads(response.content)
    finally:
        reset_current_api_token(marker)


def tool(mcp, name, **arguments):
    return rpc(mcp, "tools/call", {"name": name, "arguments": arguments})["result"]


def data(result):
    assert result["isError"] is False, result
    return result["structuredContent"]


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM"])
@pytest.mark.parametrize("surface", ["agents", "tasks", "runtimes", "tools", "resources"])
def test_scoped_discovery_matches_its_rows(mcp, selected, kind, surface):
    bind(mcp, kind, [Permission.AGENT_READ, Permission.AGENT_DISPATCH])
    if surface in {"agents", "tasks", "runtimes"}:
        result = rpc(
            mcp,
            "tools/call",
            {"name": f"astrolift_list_{surface}", "arguments": {}},
            selected=selected,
        )
        rows = data(result["result"])[surface]
        if surface == "runtimes":
            assert rows and all(row["name"] and row["image"] for row in rows)
        else:
            expected = mcp.agents[0] if surface == "agents" else mcp.tasks[0]
            assert [row["id"] for row in rows] == [str(expected.guid)]
    else:
        result = rpc(mcp, f"{surface}/list", selected=selected)
        assert "error" not in result, result
        rows = result["result"][surface]
        if surface == "resources":
            assert [row["uri"] for row in rows] == [f"astrolift://agents/{mcp.agents[0].slug}/package"]
        else:
            assert {row["name"] for row in rows} >= {
                "astrolift_list_agents",
                "astrolift_list_tasks",
                "astrolift_list_runtimes",
                "astrolift_get_agent",
                "astrolift_get_task",
                "astrolift_run_agent",
                "astrolift_cancel_task",
            }


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
@pytest.mark.parametrize("action", ["get_agent", "get_task", "cancel_task"])
def test_own_object_action_uses_its_scope(mcp, kind, action):
    bind(mcp, kind, [Permission.AGENT_READ, Permission.AGENT_DISPATCH])
    args = (
        {"agent_slug": mcp.agents[0].slug} if action == "get_agent" else {"task_id": str(mcp.tasks[0].guid)}
    )
    result = data(tool(mcp, f"astrolift_{action}", **args))
    assert result
    if action == "cancel_task":
        mcp.tasks[0].refresh_from_db()
        assert mcp.tasks[0].status == AgentTask.Status.CANCELLED


def test_recorded_project_ownership_controls_task_reads_and_cancel(mcp):
    bind(mcp, "TEAM", [Permission.AGENT_READ, Permission.AGENT_DISPATCH])
    AgentTask.objects.filter(pk=mcp.tasks[0].pk).update(project=mcp.world.platform_project)
    AgentTask.objects.filter(pk=mcp.tasks[1].pk).update(project=mcp.world.medops_project)
    result = rpc(mcp, "tools/call", {"name": "astrolift_list_tasks"}, selected=True)
    assert [row["id"] for row in data(result["result"])["tasks"]] == [str(mcp.tasks[1].guid)]
    assert tool(mcp, "astrolift_get_task", task_id=str(mcp.tasks[0].guid))["isError"]
    assert tool(mcp, "astrolift_cancel_task", task_id=str(mcp.tasks[0].guid))["isError"]
    data(tool(mcp, "astrolift_cancel_task", task_id=str(mcp.tasks[1].guid)))


def test_package_resource_reads_use_the_same_agent_scope(mcp):
    bind(mcp, "APP", [Permission.AGENT_READ])
    for agent, allowed in zip(mcp.agents, [True, False], strict=True):
        result = rpc(mcp, "resources/read", {"uri": f"astrolift://agents/{agent.slug}/package"})
        assert ("error" not in result) is allowed, result


def test_dispatch_pins_the_authorized_agent_when_a_sibling_reuses_its_slug(mcp, monkeypatch):
    bind(mcp, "APP", [Permission.AGENT_DISPATCH])
    Workload.objects.filter(pk=mcp.agents[1].pk).update(slug=mcp.agents[0].slug)
    starts = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow", lambda *args, **kwargs: starts.append(kwargs)
    )
    result = data(tool(mcp, "astrolift_run_agent", agent_slug=mcp.agents[0].slug))
    task = AgentTask.objects.get(guid=result["task_id"])
    assert task.agent_definition_id == mcp.agents[0].pk
    assert task.status == AgentTask.Status.QUEUED and len(starts) == 1


def test_team_share_read_control_and_revocation_remain_a_token_ceiling(mcp):
    bind(mcp, "ORG", [Permission.AGENT_READ, Permission.AGENT_DISPATCH])
    mcp.credential.team = mcp.world.medops
    share = AppTeamAccess.objects.create(
        registered_app=mcp.world.platform_app, team=mcp.world.medops, access_level="viewer"
    )
    assert len(data(tool(mcp, "astrolift_list_tasks"))["tasks"]) == 2
    assert tool(mcp, "astrolift_cancel_task", task_id=str(mcp.tasks[1].guid))["isError"]
    share.access_level = "deployer"
    share.save(update_fields=["access_level"])
    data(tool(mcp, "astrolift_cancel_task", task_id=str(mcp.tasks[1].guid)))
    share.deleted_at = timezone.now()
    share.save(update_fields=["deleted_at"])
    assert [row["id"] for row in data(tool(mcp, "astrolift_list_tasks"))["tasks"]] == [str(mcp.tasks[0].guid)]


def test_expired_roles_and_read_only_tokens_cannot_control_tasks(mcp):
    binding = bind(mcp, "APP", [Permission.AGENT_READ, Permission.AGENT_DISPATCH])
    mcp.credential.scopes = [SCOPE_MCP_READ]
    assert tool(mcp, "astrolift_cancel_task", task_id=str(mcp.tasks[0].guid))["isError"]
    data(tool(mcp, "astrolift_get_task", task_id=str(mcp.tasks[0].guid)))
    binding.expires_at = timezone.now()
    binding.save(update_fields=["expires_at"])
    assert not rpc(mcp, "tools/list")["result"]["tools"]
    assert tool(mcp, "astrolift_get_task", task_id=str(mcp.tasks[0].guid))["isError"]


def test_fleet_capability_discovery_does_not_widen_project_resource_tools(mcp):
    bind(mcp, "APP", [Permission.AGENT_READ, Permission.PROJECT_READ])
    names = {row["name"] for row in rpc(mcp, "tools/list")["result"]["tools"]}
    assert "astrolift_list_agents" in names
    assert "astrolift_list_project_resources" not in names
    assert tool(mcp, "astrolift_list_project_resources", project_id=str(mcp.world.medops_project.guid))[
        "isError"
    ]


def test_foreign_token_cannot_use_the_selected_organization(mcp):
    bind(mcp, "ORG", [Permission.AGENT_READ])
    mcp.credential.organization = ScopeWorld("1747-token").org
    assert tool(mcp, "astrolift_list_tasks")["isError"]


@pytest.mark.parametrize("level", ["viewer", "deployer", "owner"])
def test_secondary_team_roles_require_an_action_appropriate_share(mcp, level):
    bind(mcp, "TEAM", [Permission.AGENT_READ, Permission.AGENT_DISPATCH])
    AppTeamAccess.objects.create(
        registered_app=mcp.world.platform_app, team=mcp.world.medops, access_level=level
    )
    data(tool(mcp, "astrolift_get_task", task_id=str(mcp.tasks[1].guid)))
    result = tool(mcp, "astrolift_cancel_task", task_id=str(mcp.tasks[1].guid))
    assert result["isError"] is (level == "viewer")
    mcp.tasks[1].refresh_from_db()
    assert mcp.tasks[1].status == (
        AgentTask.Status.DRAFT if level == "viewer" else AgentTask.Status.CANCELLED
    )


@pytest.mark.parametrize("owner", ["project", "team", "definition"])
@pytest.mark.parametrize("invalid", ["deleted", "foreign"])
def test_invalid_recorded_ownership_requires_org_authority(mcp, owner, invalid):
    bind(mcp, "TEAM", [Permission.AGENT_READ])
    world = ScopeWorld("1747-owner") if invalid == "foreign" else mcp.world
    if owner == "definition":
        target = Workload.objects.create(
            registered_app=world.medops_app, name="History", slug="history", kind="agent"
        )
        field = "agent_definition"
    else:
        target = world.medops_project if owner == "project" else world.medops
        field = owner
    AgentTask.objects.filter(pk=mcp.tasks[0].pk).update(**{field: target})
    if invalid == "deleted":
        target.deleted_at = timezone.now()
        target.save(update_fields=["deleted_at"])
    args = {"task_id": str(mcp.tasks[0].guid)}
    assert tool(mcp, "astrolift_get_task", **args)["isError"]
    bind(mcp, "ORG", [Permission.AGENT_READ])
    assert data(tool(mcp, "astrolift_get_task", **args))["id"] == str(mcp.tasks[0].guid)


def test_role_filtering_precedes_task_pagination(mcp):
    bind(mcp, "APP", [Permission.AGENT_READ])
    newest = AgentTask.objects.create(organization=mcp.world.org, agent_definition=mcp.agents[0])
    AgentTask.objects.create(organization=mcp.world.org, agent_definition=mcp.agents[1])
    first = data(tool(mcp, "astrolift_list_tasks", limit=1))
    second = data(tool(mcp, "astrolift_list_tasks", limit=1, cursor=first["next_cursor"]))
    assert [row["id"] for row in first["tasks"]] == [str(newest.guid)]
    assert [row["id"] for row in second["tasks"]] == [str(mcp.tasks[0].guid)]
    assert second["next_cursor"] is None


def test_foreign_reference_metadata_does_not_cross_the_gateway(mcp):
    bind(mcp, "TEAM", [Permission.AGENT_READ])
    foreign = ScopeWorld("1747-metadata")
    agent = Workload.objects.create(
        registered_app=foreign.medops_app, name="Foreign", slug="foreign", kind="agent"
    )
    dispatcher = DispatcherInstance.objects.create(organization=foreign.org, name="Foreign", slug="foreign")
    AgentTask.objects.filter(pk=mcp.tasks[0].pk).update(
        project=mcp.world.medops_project, agent_definition=agent, dispatcher=dispatcher
    )
    task = data(tool(mcp, "astrolift_get_task", task_id=str(mcp.tasks[0].guid)))
    assert task["agent_slug"] == "" and task["dispatcher"] is None
    brief = Brief.objects.create(
        organization=foreign.org,
        content_hash="foreign",
        manifest_snapshot={"agent_package": {"private": True}},
    )
    Workload.objects.filter(pk=mcp.agents[0].pk).update(brief=brief)
    package = data(tool(mcp, "astrolift_get_agent", agent_slug=mcp.agents[0].slug))["package"]
    assert package["brief_id"] is None and package["definition"] == {}


def test_ambiguous_authorized_agents_cannot_be_read_or_dispatched(mcp):
    bind(mcp, "ORG", [Permission.AGENT_READ, Permission.AGENT_DISPATCH])
    Workload.objects.filter(pk=mcp.agents[1].pk).update(slug=mcp.agents[0].slug)
    for action in ["astrolift_get_agent", "astrolift_run_agent"]:
        result = tool(mcp, action, agent_slug=mcp.agents[0].slug)
        assert result["isError"] and result["structuredContent"]["code"] == "conflict"
    assert AgentTask.objects.count() == 3


@pytest.mark.parametrize("kind,team_token", [("APP", False), ("TEAM", False), ("ORG", True)])
def test_http_authentication_preserves_scoped_fleet_access(mcp, kind, team_token):
    bind(mcp, kind, [Permission.AGENT_READ, Permission.AGENT_DISPATCH])
    mcp.credential.team = mcp.world.medops if team_token else None
    mcp.credential.scopes = [SCOPE_MCP_READ]
    mcp.credential.save(update_fields=["team", "scopes"])
    client = Client()
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {mcp.plaintext}",
        "HTTP_ACCEPT": "application/json, text/event-stream",
    }

    def post(method, params=None):
        return client.post(
            "/api/mcp/v1/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}),
            content_type="application/json",
            **headers,
        )

    initialized = post(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "scopes", "version": "1"},
        },
    )
    assert initialized.status_code == 200
    headers.update(
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25", HTTP_MCP_SESSION_ID=initialized.headers["Mcp-Session-Id"]
    )
    response = post("tools/call", {"name": "astrolift_list_tasks", "arguments": {}})
    assert response.status_code == 200
    assert [row["id"] for row in data(response.json()["result"])["tasks"]] == [str(mcp.tasks[0].guid)]
    denied = post(
        "tools/call", {"name": "astrolift_cancel_task", "arguments": {"task_id": str(mcp.tasks[0].guid)}}
    )
    assert denied.json()["result"]["isError"]
    mcp.tasks[0].refresh_from_db()
    assert mcp.tasks[0].status == AgentTask.Status.DRAFT
    assert get_current_api_token() is None
