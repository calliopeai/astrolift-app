"""Workflow discovery over authenticated MCP with native rows and live role grants."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from django.test import Client
from django.utils import timezone

from astrolift_agents.mcp_contract import WORKFLOW_TOOL_NAMES
from astrolift_agents.models import AgentTask
from astrolift_agents.tests.test_fleet_scopes_1745 import no_opensearch as no_opensearch
from astrolift_identity.api_tokens import SCOPE_MCP_READ, mint_token
from astrolift_identity.models import ApiToken, Member
from astrolift_operations.models import WorkflowRun
from astrolift_registry.models import Workload
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user
from workflows.manifest import create_definition_from_manifest, parse_workflow_manifest
from workflows.models import (
    Workflow,
    WorkflowDefinition,
    WorkflowDefinitionStart,
    WorkflowInstance,
    WorkflowStage,
)
from workflows.reviewed_starts import definition_revision
from workflows.tests.test_manifest import FIXTURE

pytestmark = pytest.mark.django_db


@pytest.fixture
def mcp():
    world = ScopeWorld("workflow-mcp")
    foreign = ScopeWorld("workflow-mcp-foreign")
    user = make_user("workflow-mcp")
    Member.objects.create(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=world.org.pk)
    minted = mint_token()
    token = ApiToken.objects.create(
        user=user,
        organization=world.org,
        name="Workflow reader",
        token_hash=minted.token_hash,
        token_last_4=minted.last4,
        scopes=[SCOPE_MCP_READ],
    )
    definitions = []
    configured = []
    for i, project in enumerate(
        [world.medops_project, world.medops_project, world.platform_project, foreign.medops_project, None]
    ):
        org = project.organization if project else None
        if project:
            app = {
                world.medops_project.pk: world.medops_app,
                world.platform_project.pk: world.platform_app,
                foreign.medops_project.pk: foreign.medops_app,
            }[project.pk]
            Workload.objects.create(
                registered_app=app, name=f"Coder {i}", slug=f"coder-{i}", kind=Workload.Kind.AGENT
            )
        definition = create_definition_from_manifest(
            parse_workflow_manifest(
                FIXTURE.replace("feature-dev", f"feature-{i}").replace("my-coder", f"coder-{i}")
            ),
            organization=org,
        )
        definition.project = project
        definition.name = f"MCP scope 2301 Workflow {i}"
        definition.save(update_fields=["project", "name"])
        definitions.append(definition)
        if org:
            configured.append(
                Workflow.objects.create(
                    organization=org,
                    definition=definition,
                    name=f"MCP scope 2301 Configured {i}",
                    slug=f"configured-{i}",
                    inputs={"topic": "release notes"},
                    is_enabled=False,
                )
            )
    return SimpleNamespace(
        world=world,
        foreign=foreign,
        user=user,
        token=token,
        client=Client(),
        headers={
            "HTTP_AUTHORIZATION": f"Bearer {minted.plaintext}",
            "HTTP_ACCEPT": "application/json, text/event-stream",
        },
        definitions=definitions,
        configured=configured,
    )


def grant(mcp, kind="PROJECT", scope=None):
    return bind_role(
        mcp.user,
        permissions=[Permission.WORKFLOW_READ],
        kind=kind,
        scope_id=(
            scope
            or {"PROJECT": mcp.world.medops_project, "TEAM": mcp.world.medops, "ORG": mcp.world.org}[kind]
        ).pk,
        slug=f"workflow-mcp-{uuid4().hex}",
    )


def rpc(mcp, method, params=None):
    return mcp.client.post(
        "/api/mcp/v1/",
        content_type="application/json",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}),
        **mcp.headers,
    )


def tool(mcp, name, **arguments):
    response = rpc(mcp, "tools/call", {"name": f"astrolift_{name}", "arguments": arguments})
    assert response.status_code == 200, response.content
    return response.json()["result"]


def data(result):
    assert not result["isError"], result
    return result["structuredContent"]


@pytest.mark.parametrize("kind", ["PROJECT", "TEAM", "ORG"])
@pytest.mark.parametrize("surface", ["workflow_definitions", "workflows"])
def test_authenticated_discovery_pages_only_permitted_rows(mcp, kind, surface):
    grant(mcp, kind)
    expected = mcp.definitions if surface == "workflow_definitions" else mcp.configured
    allowed = [expected[0], expected[1]]
    if kind == "ORG":
        allowed.append(expected[2])
        if surface == "workflow_definitions":
            allowed.append(expected[4])
    seen = []
    cursor = None
    for _ in range(len(allowed)):
        # Organization readers also see migration-seeded global templates.
        page = data(
            tool(
                mcp,
                f"list_{surface}",
                search="MCP scope 2301",
                limit=1,
                **({"cursor": cursor} if cursor else {}),
            )
        )
        assert page["total_count"] == len(allowed)
        assert len(page["items"]) == 1
        seen.append(page["items"][0]["guid"])
        cursor = page["next_cursor"]
    assert cursor is None
    assert set(seen) == {str(row.guid) for row in allowed}
    assert len(seen) == len(set(seen))
    term = "Workflow 1" if surface == "workflow_definitions" else "Configured 1"
    matched = data(tool(mcp, f"list_{surface}", search=term))
    assert [item["guid"] for item in matched["items"]] == [str(expected[1].guid)]


def test_project_filter_and_selected_headers_cannot_expand_scope(mcp):
    grant(mcp)
    mcp.headers["HTTP_X_ASTROLIFT_TEAM"] = str(mcp.world.medops.guid)
    mcp.headers["HTTP_X_ASTROLIFT_PROJECT"] = str(mcp.world.medops_project.guid)
    for project in [mcp.world.platform_project, mcp.foreign.medops_project]:
        page = data(tool(mcp, "list_workflow_definitions", project_id=str(project.guid)))
        assert page["items"] == []
    result = data(tool(mcp, "get_workflow_definition", definition_id=str(mcp.definitions[0].guid)))
    assert result["definition"]["guid"] == str(mcp.definitions[0].guid)
    mcp.definitions[0].refresh_from_db()
    assert result["definition"]["revision"] == definition_revision(mcp.definitions[0])
    assert result["definition"]["input_contract"]["supported"] is True


@pytest.mark.parametrize("target", [2, 3, 4])
@pytest.mark.parametrize("name", ["get_workflow_definition", "export_workflow_manifest"])
def test_exact_target_checks_never_use_selected_team_as_authority(mcp, target, name):
    grant(mcp)
    mcp.token.team = mcp.world.medops
    mcp.token.save(update_fields=["team"])
    result = tool(mcp, name, definition_id=str(mcp.definitions[target].guid))
    assert result["isError"]
    assert result["structuredContent"]["code"] == "permission_denied"


@pytest.mark.parametrize(
    "change",
    ["revoke_role", "revoke_token", "delete_project", "delete_team", "foreign_grant", "missing_read_scope"],
)
def test_authority_is_checked_again_on_each_request(mcp, change):
    binding = grant(mcp)
    assert WORKFLOW_TOOL_NAMES <= {row["name"] for row in rpc(mcp, "tools/list").json()["result"]["tools"]}
    if change == "revoke_role":
        binding.deleted_at = timezone.now()
        binding.save(update_fields=["deleted_at"])
    elif change == "revoke_token":
        mcp.token.is_revoked = True
        mcp.token.save(update_fields=["is_revoked"])
    elif change in {"delete_project", "delete_team"}:
        row = mcp.world.medops_project if change == "delete_project" else mcp.world.medops
        row.deleted_at = timezone.now()
        row.save(update_fields=["deleted_at"])
    elif change == "foreign_grant":
        binding.scope_id = mcp.foreign.medops_project.pk
        binding.save(update_fields=["scope_id"])
    else:
        mcp.token.scopes = ["mcp:write"]
        mcp.token.save(update_fields=["scopes"])
    response = rpc(
        mcp,
        "tools/call",
        {
            "name": "astrolift_get_workflow_definition",
            "arguments": {"definition_id": str(mcp.definitions[0].guid)},
        },
    )
    if change == "revoke_token":
        assert response.status_code == 401
    else:
        assert response.json()["result"]["isError"]
        assert response.json()["result"]["structuredContent"]["code"] == "permission_denied"


@pytest.mark.parametrize("surface", ["workflow_definitions", "workflows"])
def test_org_grant_is_narrowed_by_credential_team(mcp, surface):
    grant(mcp, "ORG")
    mcp.token.team = mcp.world.medops
    mcp.token.save(update_fields=["team"])
    page = data(tool(mcp, f"list_{surface}"))
    expected = mcp.definitions if surface == "workflow_definitions" else mcp.configured
    assert {row["guid"] for row in page["items"]} == {str(row.guid) for row in expected[:2]}
    denied = tool(mcp, "get_workflow_definition", definition_id=str(mcp.definitions[2].guid))
    assert denied["structuredContent"]["code"] == "permission_denied"


def test_review_contract_changes_with_native_definition_and_never_replaces_a_deleted_id(mcp):
    grant(mcp, "ORG")
    definition = mcp.definitions[0]
    first = data(tool(mcp, "get_workflow_definition", definition_id=str(definition.guid)))["definition"]
    definition.input_schema = {
        "type": "object",
        "properties": {"topic": {"type": "string"}},
        "required": ["topic"],
        "additionalProperties": False,
    }
    definition.save(update_fields=["input_schema"])
    second = data(tool(mcp, "get_workflow_definition", definition_id=str(definition.guid)))["definition"]
    assert second["revision"] != first["revision"]
    assert second["input_contract"]["digest"] != first["input_contract"]["digest"]
    assert second["input_contract"]["schema"] == definition.input_schema
    definition.deleted_at = timezone.now()
    definition.save(update_fields=["deleted_at"])
    create_definition_from_manifest(
        parse_workflow_manifest(FIXTURE.replace("feature-dev", definition.slug)),
        organization=mcp.world.org,
    )
    assert (
        data(tool(mcp, "get_workflow_definition", definition_id=str(definition.guid)))["definition"] is None
    )
    exported = data(tool(mcp, "export_workflow_manifest", definition_id=str(definition.guid)))
    assert not exported["ok"] and exported["toml"] is None


def test_preview_and_exact_export_use_native_grammar_without_mutations(mcp):
    grant(mcp, "ORG")
    models = [
        WorkflowDefinition,
        WorkflowStage,
        Workflow,
        WorkflowDefinitionStart,
        WorkflowInstance,
        WorkflowRun,
        AgentTask,
    ]
    before = {model: list(model.objects.order_by("pk").values()) for model in models}
    preview = data(tool(mcp, "preview_workflow_manifest", toml=FIXTURE))
    assert preview["ok"]
    assert [row["kind"] for row in preview["stages"]] == ["agent_dispatch", "human_gate"]
    for definition in [mcp.definitions[0], mcp.definitions[4]]:
        exported = data(tool(mcp, "export_workflow_manifest", definition_id=str(definition.guid)))
        assert exported["ok"]
        parsed = parse_workflow_manifest(exported["toml"])
        assert parsed.definition.slug == definition.slug
        assert len(parsed.stages) == 2
    broken = data(tool(mcp, "preview_workflow_manifest", toml="[workflow\n"))
    assert not broken["ok"] and broken["error"]
    assert broken["error_line"] is not None
    assert {model: list(model.objects.order_by("pk").values()) for model in models} == before


@pytest.mark.django_db(transaction=True)
def test_preview_does_not_dispatch_to_real_temporal(mcp):
    from temporalio.client import Client as TemporalClient

    async def execution_ids():
        client = await TemporalClient.connect(
            os.environ.get("ASTROLIFT_TEST_TEMPORAL_ADDRESS") or os.environ["TEMPORAL_ADDRESS"],
            namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        )
        return {(row.id, row.run_id) async for row in client.list_workflows()}

    grant(mcp)
    before = async_to_sync(execution_ids)()
    assert data(tool(mcp, "preview_workflow_manifest", toml=FIXTURE))["ok"]
    assert data(tool(mcp, "export_workflow_manifest", definition_id=str(mcp.definitions[0].guid)))["ok"]
    assert async_to_sync(execution_ids)() == before


def test_exact_export_never_substitutes_a_foreign_same_slug_even_for_superuser(mcp):
    grant(mcp, "ORG")
    mcp.user.is_superuser = True
    mcp.user.save(update_fields=["is_superuser"])
    foreign = mcp.definitions[3]
    foreign.slug = mcp.definitions[0].slug
    foreign.save(update_fields=["slug"])
    own = data(tool(mcp, "export_workflow_manifest", definition_id=str(mcp.definitions[0].guid)))
    assert parse_workflow_manifest(own["toml"]).definition.name == mcp.definitions[0].name
    result = data(tool(mcp, "export_workflow_manifest", definition_id=str(foreign.guid)))
    assert not result["ok"] and result["toml"] is None


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_workflow_definition", {"definition_id": "invalid"}),
        ("list_workflow_definitions", {"project_id": "invalid"}),
        ("list_workflows", {"limit": 201}),
        ("list_workflows", {"cursor": "x" * 4097}),
        ("preview_workflow_manifest", {"toml": "x" * 262145}),
        ("preview_workflow_manifest", {"toml": FIXTURE, "confirmed": True}),
    ],
)
def test_invalid_and_oversized_arguments_are_rejected(mcp, name, args):
    grant(mcp)
    result = tool(mcp, name, **args)
    assert result["isError"] and result["structuredContent"]["code"] == "invalid_arguments"


def test_disabled_workflows_are_neither_advertised_nor_callable(mcp, monkeypatch):
    grant(mcp, "ORG")
    monkeypatch.setenv("FEATURE_WORKFLOWS", "false")
    names = {row["name"] for row in rpc(mcp, "tools/list").json()["result"]["tools"]}
    assert not names & WORKFLOW_TOOL_NAMES
    for name in WORKFLOW_TOOL_NAMES:
        result = tool(mcp, name.removeprefix("astrolift_"))
        assert result["isError"] and result["structuredContent"]["code"] == "not_found"
