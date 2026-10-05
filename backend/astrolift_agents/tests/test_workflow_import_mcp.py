"""Authenticated MCP import delegates to the native project-aware authoring path."""

import json
import os
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync

from astrolift_agents.tests.test_workflow_mcp import (
    data,
    rpc,
    tool,
)
from astrolift_agents.tests.test_workflow_mcp import (
    mcp as mcp_fixture,
)
from astrolift_agents.tests.test_workflow_mcp import (
    no_opensearch as no_opensearch,
)
from astrolift_workflows.tests.test_manifest_schema import VALID_TOML
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role
from workflows.models import WorkflowDefinition, WorkflowDefinitionStart, WorkflowInstance

mcp = mcp_fixture
pytestmark = pytest.mark.django_db
NAME = "astrolift_import_workflow_manifest"


def authorize(mcp, *, permissions=(Permission.WORKFLOW_CREATE,), scopes=("mcp:write", "workflow:write")):
    mcp.token.scopes = list(scopes)
    mcp.token.team = mcp.world.medops
    mcp.token.save(update_fields=["scopes", "team"])
    return bind_role(
        mcp.user,
        permissions=permissions,
        kind="PROJECT",
        scope_id=mcp.world.medops_project.pk,
        slug=f"workflow-import-{uuid4().hex}",
    )


def invoke(mcp, **args):
    return tool(
        mcp,
        "import_workflow_manifest",
        **{"toml": VALID_TOML, "project_id": str(mcp.world.medops_project.guid), **args},
    )


def test_import_defaults_to_preview_then_persists_disabled_exact_id(mcp):
    authorize(mcp)
    assert NAME in {row["name"] for row in rpc(mcp, "tools/list").json()["result"]["tools"]}
    before = WorkflowDefinition.objects.count()
    preview = data(invoke(mcp))
    assert preview["ok"] and preview["definition_id"] is None
    assert WorkflowDefinition.objects.count() == before
    result = invoke(mcp, preview=False)
    imported = data(result)
    assert json.loads(result["content"][0]["text"]) == imported
    definition = WorkflowDefinition.objects.get(guid=imported["definition_id"])
    assert definition.project == mcp.world.medops_project
    assert definition.slug == imported["created_slug"]
    assert not definition.is_enabled
    assert not WorkflowDefinitionStart.objects.exists()
    assert not WorkflowInstance.objects.exists()


@pytest.mark.parametrize(
    "scopes", [("mcp:write",), ("workflow:write",), ("mcp:read",), ("mcp:dispatch", "workflow:trigger")]
)
def test_import_requires_both_mcp_and_workflow_write_scopes(mcp, scopes):
    authorize(mcp, scopes=scopes)
    before = WorkflowDefinition.objects.count()
    listed = rpc(mcp, "tools/list")
    if scopes == ("workflow:write",):
        assert listed.status_code == 403
        called = rpc(mcp, "tools/call", {"name": NAME, "arguments": {"toml": VALID_TOML, "preview": False}})
        assert called.status_code == 403
    else:
        assert NAME not in {row["name"] for row in listed.json()["result"]["tools"]}
        assert invoke(mcp, preview=False)["isError"]
    assert WorkflowDefinition.objects.count() == before


@pytest.mark.parametrize("target", ["sibling", "foreign", "org"])
def test_import_cannot_expand_project_token_destination(mcp, target):
    authorize(mcp)
    bind_role(
        mcp.user,
        permissions=[Permission.WORKFLOW_CREATE],
        kind="ORG",
        scope_id=mcp.world.org.pk,
        slug="org-create",
    )
    before = WorkflowDefinition.objects.count()
    args = {"toml": VALID_TOML, "preview": False}
    if target != "org":
        project = mcp.world.platform_project if target == "sibling" else mcp.foreign.medops_project
        args["project_id"] = str(project.guid)
    result = tool(mcp, "import_workflow_manifest", **args)
    assert result["isError"]
    assert result["structuredContent"]["code"] == "permission_denied"
    assert WorkflowDefinition.objects.count() == before


def test_import_native_error_remains_structured_for_both_mcp_consumers(mcp):
    authorize(mcp)
    result = invoke(mcp, toml="[workflow\n", preview=False)
    assert result["isError"]
    payload = result["structuredContent"]
    assert not payload["ok"]
    assert payload["manifest"]["error_line"] is not None
    assert payload["errors"][0]["field"] == "toml"
    assert json.loads(result["content"][0]["text"]) == payload


@pytest.mark.parametrize(
    "args", [{"project_id": "bad"}, {"toml": "x" * 262145}, {"preview": "false"}, {"unexpected": True}]
)
def test_import_rejects_invalid_arguments_before_writes(mcp, args):
    authorize(mcp)
    before = WorkflowDefinition.objects.count()
    result = invoke(mcp, **args)
    assert result["isError"]
    assert result["structuredContent"]["code"] == "invalid_arguments"
    assert WorkflowDefinition.objects.count() == before


def test_import_replace_requires_update_and_retains_project_owner(mcp):
    authorize(mcp)
    first = data(invoke(mcp, preview=False))
    before = WorkflowDefinition.objects.count()
    result = invoke(mcp, preview=False, replace=True)
    assert result["isError"]
    assert WorkflowDefinition.objects.count() == before
    authorize(mcp, permissions=(Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE))
    result = data(
        invoke(mcp, preview=False, replace=True, toml=VALID_TOML + '\n[[stage]]\nkind="human_gate"\n')
    )
    assert result["mode"] == "versioned"
    assert result["definition_id"] != first["definition_id"]
    assert WorkflowDefinition.objects.get(guid=result["definition_id"]).project == mcp.world.medops_project


def test_import_respects_feature_gate(mcp, monkeypatch):
    authorize(mcp)
    monkeypatch.setenv("FEATURE_WORKFLOWS", "false")
    assert NAME not in {row["name"] for row in rpc(mcp, "tools/list").json()["result"]["tools"]}
    assert invoke(mcp, preview=False)["structuredContent"]["code"] == "not_found"


@pytest.mark.django_db(transaction=True)
def test_new_import_does_not_start_real_temporal_execution(mcp):
    from temporalio.client import Client

    async def executions():
        client = await Client.connect(os.environ["ASTROLIFT_TEST_TEMPORAL_ADDRESS"])
        return {(row.id, row.run_id) async for row in client.list_workflows()}

    authorize(mcp)
    before = async_to_sync(executions)()
    assert data(invoke(mcp, preview=False))["ok"]
    assert async_to_sync(executions)() == before
