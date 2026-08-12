"""Remote MCP auth, capability filtering, dispatch, and hard-stop tests."""

from __future__ import annotations

import hashlib
import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, override_settings

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, Brief
from astrolift_agents.views.mcp import mcp_gateway
from astrolift_identity.api_tokens import SCOPE_MCP_DISPATCH, SCOPE_MCP_READ, SCOPE_MCP_WRITE
from astrolift_identity.models import ApiToken, Organization, Project, Team
from astrolift_operations.models import AuditEvent
from astrolift_registry.models import AppTeamAccess, RegisteredApp, Workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _token(org, *, scopes, team=None):
    user = get_user_model().objects.create_user(
        username=f"mcp-{org.slug}",
        email=f"mcp-{org.slug}@example.test",
    )
    row = ApiToken.objects.create(
        user=user,
        organization=org,
        team=team,
        name="mcp-test",
        token_hash=hashlib.sha256(b"unused-plaintext").hexdigest(),
        token_last_4="test",
        scopes=list(scopes),
    )
    return user, row


def _request(user, token, method: str, params=None, *, request_id=1, origin=""):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    request = RequestFactory().post(
        "/api/mcp/v1/",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_ORIGIN=origin,
        HTTP_ACCEPT="application/json, text/event-stream",
    )
    request.user = user
    request._api_token = token
    return request


def _call(org, user, token, method: str, params=None):
    with tenant_context(
        TenantContext(
            organization_id=org.pk,
            team_id=token.team_id,
            actor_user_id=user.pk,
        )
    ):
        response = mcp_gateway(_request(user, token, method, params))
    return response, json.loads(response.content or b"{}")


def _agent(org, *, slug="triage", team=None):
    team = team or Team.objects.create(organization=org, name="MCP Team", slug=f"mcp-{org.slug}")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="MCP Agent",
        slug=f"mcp-agent-{org.slug}-{slug}",
        source_repo="acme/agents",
        manifest_path=f"agents/{slug}/astrolift.toml",
        deploy_branch="main",
        provisioning_status=RegisteredApp.ProvisioningStatus.READY,
    )
    brief = Brief.objects.create(
        organization=org,
        registered_app=app,
        content_hash=hashlib.sha256(f"{org.pk}:{slug}".encode()).hexdigest(),
        status=Brief.Status.READY,
        manifest_snapshot={
            "requires_payload": True,
            "payload_storage_ready": True,
            "agent_package": {
                "schema": "astrolift.agent.package/v1",
                "agent": {"name": slug},
                "skills": [{"slug": "triage"}],
                "tools": [{"slug": "agent-report"}],
            },
        },
    )
    return Workload.objects.create(
        registered_app=app,
        name="Triage",
        slug=slug,
        kind=Workload.Kind.AGENT,
        brief=brief,
        tool_timeout_seconds=900,
    )


def test_initialize_requires_api_token():
    request = RequestFactory().post(
        "/api/mcp/v1/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        content_type="application/json",
    )
    request.user = AnonymousUser()
    response = mcp_gateway(request)
    assert response.status_code == 401


def test_tool_list_is_filtered_by_scope_and_rbac(permission_resolver):
    org = Organization.objects.create(name="MCP Org", slug="mcp-org")
    user, token = _token(org, scopes=[SCOPE_MCP_READ])
    permission_resolver.grant(Permission.AGENT_READ)

    response, payload = _call(org, user, token, "tools/list")

    assert response.status_code == 200
    names = {tool["name"] for tool in payload["result"]["tools"]}
    assert {"astrolift_list_agents", "astrolift_get_agent", "astrolift_get_task"} <= names
    assert "astrolift_run_agent" not in names
    assert "astrolift_cancel_task" not in names
    assert "astrolift_sync_agent_repo" not in names


def test_dispatch_only_token_can_connect_without_read_scope(permission_resolver):
    org = Organization.objects.create(name="MCP Dispatch Org", slug="mcp-dispatch-only")
    user, token = _token(org, scopes=[SCOPE_MCP_DISPATCH])
    permission_resolver.grant(Permission.AGENT_DISPATCH)

    response, payload = _call(org, user, token, "tools/list")

    assert response.status_code == 200
    names = {tool["name"] for tool in payload["result"]["tools"]}
    assert names == {"astrolift_run_agent", "astrolift_cancel_task"}


def test_denied_tool_call_is_audited(permission_resolver):
    org = Organization.objects.create(name="MCP Org", slug="mcp-denied-audit")
    user, token = _token(org, scopes=[SCOPE_MCP_READ])
    permission_resolver.grant(Permission.AGENT_READ)

    _, payload = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_run_agent", "arguments": {"agent_slug": "missing"}},
    )

    assert payload["result"]["isError"] is True
    event = AuditEvent.objects.get(organization=org, action="mcp.tool.astrolift_run_agent")
    assert event.decision == "DENY"
    assert "mcp:dispatch" in event.data["error"]


def test_read_tools_return_source_state_and_canonical_package(permission_resolver):
    org = Organization.objects.create(name="MCP Org", slug="mcp-read")
    user, token = _token(org, scopes=[SCOPE_MCP_READ])
    permission_resolver.grant(Permission.AGENT_READ)
    _agent(org)

    _, listed = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_list_agents", "arguments": {}},
    )
    row = listed["result"]["structuredContent"]["agents"][0]
    assert row["source"]["repo"] == "acme/agents"
    assert row["source"]["agent_auto_sync"] is False
    assert row["package"]["payload_storage_ready"] is True

    _, detail = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_get_agent", "arguments": {"agent_slug": "triage"}},
    )
    package = detail["result"]["structuredContent"]["package"]["definition"]
    assert package["schema"] == "astrolift.agent.package/v1"
    assert package["tools"][0]["slug"] == "agent-report"


def test_team_scoped_token_cannot_read_or_dispatch_another_teams_agent(permission_resolver, monkeypatch):
    org = Organization.objects.create(name="MCP Org", slug="mcp-team-scope")
    team_a = Team.objects.create(organization=org, name="Team A", slug="mcp-team-a")
    team_b = Team.objects.create(organization=org, name="Team B", slug="mcp-team-b")
    user, token = _token(
        org,
        team=team_a,
        scopes=[SCOPE_MCP_READ, SCOPE_MCP_DISPATCH],
    )
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _agent(org, slug="allowed", team=team_a)
    hidden = _agent(org, slug="hidden", team=team_b)
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", lambda *_a, **_k: None)

    _, listed = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_list_agents", "arguments": {}},
    )
    assert [item["slug"] for item in listed["result"]["structuredContent"]["agents"]] == ["allowed"]

    _, detail = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_get_agent", "arguments": {"agent_slug": hidden.slug}},
    )
    assert detail["result"]["isError"] is True
    assert detail["result"]["structuredContent"]["code"] == "not_found"

    _, dispatched = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_run_agent", "arguments": {"agent_slug": hidden.slug}},
    )
    assert dispatched["result"]["isError"] is True
    assert dispatched["result"]["structuredContent"]["code"] == "not_found"
    assert AgentTask.objects.filter(organization=org).count() == 0


def test_team_scoped_token_can_read_explicitly_shared_agent(permission_resolver):
    org = Organization.objects.create(name="MCP Org", slug="mcp-team-shared")
    team_a = Team.objects.create(organization=org, name="Team A", slug="mcp-share-a")
    team_b = Team.objects.create(organization=org, name="Team B", slug="mcp-share-b")
    user, token = _token(org, team=team_a, scopes=[SCOPE_MCP_READ])
    permission_resolver.grant(Permission.AGENT_READ)
    shared = _agent(org, slug="shared", team=team_b)
    AppTeamAccess.objects.create(
        registered_app=shared.registered_app,
        team=team_a,
        access_level=AppTeamAccess.AccessLevel.VIEWER,
    )

    _, listed = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_list_agents", "arguments": {}},
    )

    assert [item["slug"] for item in listed["result"]["structuredContent"]["agents"]] == ["shared"]


def test_viewer_share_does_not_grant_dispatch(permission_resolver, monkeypatch):
    org = Organization.objects.create(name="MCP Viewer Org", slug="mcp-viewer-dispatch")
    viewer_team = Team.objects.create(organization=org, name="Viewer Team", slug="mcp-viewer-team")
    owner_team = Team.objects.create(organization=org, name="Owner Team", slug="mcp-owner-team")
    user, token = _token(
        org,
        team=viewer_team,
        scopes=[SCOPE_MCP_READ, SCOPE_MCP_DISPATCH],
    )
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    shared = _agent(org, slug="viewer-shared", team=owner_team)
    AppTeamAccess.objects.create(
        registered_app=shared.registered_app,
        team=viewer_team,
        access_level=AppTeamAccess.AccessLevel.VIEWER,
    )
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", lambda *_a, **_k: None)

    _, listed = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_get_agent", "arguments": {"agent_slug": shared.slug}},
    )
    _, dispatched = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_run_agent", "arguments": {"agent_slug": shared.slug}},
    )

    assert listed["result"]["isError"] is False
    assert dispatched["result"]["isError"] is True
    assert dispatched["result"]["structuredContent"]["code"] == "not_found"
    assert AgentTask.objects.filter(organization=org).count() == 0


def test_dispatch_and_cancel_are_scoped_and_share_runtime_contract(permission_resolver, monkeypatch):
    org = Organization.objects.create(name="MCP Org", slug="mcp-run")
    user, token = _token(org, scopes=[SCOPE_MCP_READ, SCOPE_MCP_DISPATCH])
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _agent(org)
    starts = []

    def _start(name, args, *, workflow_id, task_queue=None):
        starts.append((name, args, workflow_id))
        return None

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _start)

    _, run = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_run_agent", "arguments": {"agent_slug": "triage"}},
    )
    run_result = run["result"]
    assert run_result["isError"] is False
    task = run_result["structuredContent"]
    assert task["status"] == "queued"
    assert task["timeout_seconds"] == 900
    assert starts[0][0] == "DispatchAgentTaskWorkflow"
    assert starts[0][1][0].actor.kind == "api_token"

    _, cancelled = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_cancel_task", "arguments": {"task_id": task["task_id"]}},
    )
    assert cancelled["result"]["isError"] is False
    assert cancelled["result"]["structuredContent"]["status"] == "cancelled"
    assert cancelled["result"]["structuredContent"]["workload_deleted"] is True


def test_dispatch_start_failure_returns_error_and_terminalizes_task(permission_resolver, monkeypatch):
    org = Organization.objects.create(name="MCP Org", slug="mcp-start-failure")
    user, token = _token(org, scopes=[SCOPE_MCP_READ, SCOPE_MCP_DISPATCH])
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _agent(org)

    def _fail_start(*_args, **_kwargs):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _fail_start)

    _, response = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_run_agent", "arguments": {"agent_slug": "triage"}},
    )

    assert response["result"]["isError"] is True
    task = AgentTask.objects.get(organization=org)
    assert task.status == AgentTask.Status.FAILED
    assert "workflow failed to start" in task.failure["message"]


def test_dispatch_rejects_service_family_without_creating_task(permission_resolver):
    org = Organization.objects.create(name="MCP Org", slug="mcp-service-family")
    user, token = _token(org, scopes=[SCOPE_MCP_READ, SCOPE_MCP_DISPATCH])
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    workload = _agent(org)
    workload.run_family = Workload.RunFamily.SERVICE
    workload.save(update_fields=["run_family", "updated_at", "version"])

    _, response = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_run_agent", "arguments": {"agent_slug": "triage"}},
    )

    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["code"] == "precondition"
    assert AgentTask.objects.filter(organization=org).count() == 0


def test_dispatch_rejects_timeout_above_platform_limit(permission_resolver):
    org = Organization.objects.create(name="MCP Org", slug="mcp-timeout-limit")
    user, token = _token(org, scopes=[SCOPE_MCP_READ, SCOPE_MCP_DISPATCH])
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    workload = _agent(org)
    workload.tool_timeout_seconds = 604801
    workload.save(update_fields=["tool_timeout_seconds", "updated_at", "version"])

    _, response = _call(
        org,
        user,
        token,
        "tools/call",
        {"name": "astrolift_run_agent", "arguments": {"agent_slug": "triage"}},
    )

    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["code"] == "validation"
    assert AgentTask.objects.filter(organization=org).count() == 0


def test_dispatch_rejects_environment_spec_owned_by_another_agent(permission_resolver, monkeypatch):
    org = Organization.objects.create(name="MCP Org", slug="mcp-env-isolation")
    team = Team.objects.create(organization=org, name="Agent Team", slug="mcp-env-team")
    user, token = _token(
        org,
        team=team,
        scopes=[SCOPE_MCP_READ, SCOPE_MCP_DISPATCH],
    )
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _agent(org, slug="triage", team=team)
    foreign = AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Other agent runtime",
        slug="other-agent",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        secret_refs=[{"uri": "sm:other", "env_var": "OTHER_TOKEN"}],
    )
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", lambda *_a, **_k: None)

    _, response = _call(
        org,
        user,
        token,
        "tools/call",
        {
            "name": "astrolift_run_agent",
            "arguments": {
                "agent_slug": "triage",
                "environment_spec_id": str(foreign.guid),
            },
        },
    )

    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["code"] == "not_found"
    assert AgentTask.objects.filter(organization=org).count() == 0


def test_disallowed_browser_origin_is_rejected(settings, permission_resolver):
    settings.MCP_ALLOWED_ORIGINS = ("https://astrolift.example",)
    org = Organization.objects.create(name="MCP Org", slug="mcp-origin")
    user, token = _token(org, scopes=[SCOPE_MCP_READ])
    permission_resolver.grant(Permission.AGENT_READ)
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        response = mcp_gateway(_request(user, token, "initialize", origin="https://attacker.example"))
    assert response.status_code == 403


def test_import_tool_previews_and_persists_only_runnable_packages(permission_resolver):
    org = Organization.objects.create(name="MCP Org", slug="mcp-import")
    team = Team.objects.create(organization=org, name="Import Team", slug="mcp-import-team")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Import Project",
        slug="mcp-import-project",
    )
    user, token = _token(org, scopes=[SCOPE_MCP_READ, SCOPE_MCP_WRITE])
    permission_resolver.grant(Permission.AGENT_CREATE)

    _, preview = _call(
        org,
        user,
        token,
        "tools/call",
        {
            "name": "astrolift_import_agent_spec",
            "arguments": {
                "format": "agents_md",
                "payload": {"name": "Review", "content": "Review the change."},
                "options": {"runtime_image": "example/agent:sha-1"},
            },
        },
    )
    assert preview["result"]["isError"] is False
    assert preview["result"]["structuredContent"]["runnable"] is True
    assert preview["result"]["structuredContent"]["persisted"] is None

    _, markdown_preview = _call(
        org,
        user,
        token,
        "tools/call",
        {
            "name": "astrolift_import_agent_spec",
            "arguments": {
                "format": "agents_md",
                "payload": "Review the change adversarially.",
                "options": {"runtime_image": "example/agent:sha-1"},
            },
        },
    )
    assert markdown_preview["result"]["isError"] is False
    assert (
        markdown_preview["result"]["structuredContent"]["package"]["prompt"]["brief"]
        == "Review the change adversarially."
    )

    _, persisted = _call(
        org,
        user,
        token,
        "tools/call",
        {
            "name": "astrolift_import_agent_spec",
            "arguments": {
                "format": "agents_md",
                "payload": {"name": "Review", "content": "Review the change."},
                "options": {"runtime_image": "example/agent:sha-1"},
                "persist": True,
                "project_id": str(project.guid),
                "slug": "review-import",
            },
        },
    )
    result = persisted["result"]["structuredContent"]
    assert persisted["result"]["isError"] is False
    assert result["persisted"]["agent_slug"] == "review-import"


def test_streamable_http_auth_scope_tenant_and_session_run_through_middleware(permission_resolver):
    org = Organization.objects.create(name="MCP Org", slug="mcp-http")
    _user, token = _token(org, scopes=[SCOPE_MCP_READ])
    plaintext = "alft_at_http-integration-token"
    token.token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    token.save(update_fields=["token_hash", "updated_at", "version"])
    permission_resolver.grant(Permission.AGENT_READ)
    client = Client()
    common = {
        "HTTP_AUTHORIZATION": f"Bearer {plaintext}",
        "HTTP_ACCEPT": "application/json, text/event-stream",
    }

    initialized = client.post(
        "/api/mcp/v1/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1"},
                },
            }
        ),
        content_type="application/json",
        **common,
    )
    assert initialized.status_code == 200
    assert initialized.json()["result"]["protocolVersion"] == "2025-11-25"
    session_id = initialized.headers["Mcp-Session-Id"]

    listed = client.post(
        "/api/mcp/v1/",
        data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        HTTP_MCP_SESSION_ID=session_id,
        **common,
    )
    assert listed.status_code == 200
    assert {tool["name"] for tool in listed.json()["result"]["tools"]} >= {
        "astrolift_list_agents",
        "astrolift_get_agent",
    }

    missing_session = client.post(
        "/api/mcp/v1/",
        data=json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        **common,
    )
    assert missing_session.status_code == 400
    assert "Mcp-Session-Id is required" in missing_session.json()["error"]["message"]


@override_settings(MCP_MAX_REQUEST_BYTES=64)
def test_streamable_http_rejects_oversized_request_before_json_parse():
    org = Organization.objects.create(name="MCP Org", slug="mcp-body-limit")
    user, token = _token(org, scopes=[SCOPE_MCP_READ])
    request = _request(
        user,
        token,
        "initialize",
        {"padding": "x" * 128},
    )

    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        response = mcp_gateway(request)

    assert response.status_code == 413
    assert "request body exceeds 64 bytes" in json.loads(response.content)["error"]["message"]


def test_streamable_http_rejects_nonstandard_nonfinite_json():
    org = Organization.objects.create(name="MCP Org", slug="mcp-finite-json")
    user, token = _token(org, scopes=[SCOPE_MCP_READ])
    request = RequestFactory().post(
        "/api/mcp/v1/",
        data=(
            b'{"jsonrpc":"2.0","id":1,"method":"initialize",'
            b'"params":{"bad":NaN}}'
        ),
        content_type="application/json",
        HTTP_ACCEPT="application/json, text/event-stream",
    )
    request.user = user
    request._api_token = token

    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        response = mcp_gateway(request)

    assert response.status_code == 200
    assert json.loads(response.content)["error"]["code"] == -32700
