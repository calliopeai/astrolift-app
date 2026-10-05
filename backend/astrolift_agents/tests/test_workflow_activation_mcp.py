"""Reviewed native definition activation through MCP, followed by real execution."""

import asyncio
import json
import os
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone

from astrolift_agents.tests.test_workflow_execution_mcp import connect_real_engine, reviewed_args
from astrolift_agents.tests.test_workflow_execution_mcp import execution as execution_fixture
from astrolift_agents.tests.test_workflow_execution_mcp import mcp as mcp_fixture
from astrolift_agents.tests.test_workflow_mcp import data, rpc, tool
from astrolift_agents.tests.test_workflow_mcp import no_opensearch as no_opensearch
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role
from workflows.models import WorkflowDefinition, WorkflowDefinitionStart, WorkflowStage
from workflows.reviewed_starts import definition_revision

mcp = mcp_fixture
execution = execution_fixture
pytestmark = pytest.mark.django_db(transaction=True)
TOOL = "astrolift_set_workflow_definition_enabled"


@pytest.fixture
def activation(execution):
    execution.token.scopes += ["mcp:write", "workflow:write"]
    execution.token.save(update_fields=["scopes"])
    bind_role(
        execution.user,
        permissions=[Permission.WORKFLOW_UPDATE],
        kind="PROJECT",
        scope_id=execution.world.medops_project.pk,
        slug=f"activation-{uuid4().hex}",
    )
    execution.definition.is_enabled = False
    execution.definition.save()
    return execution


def activate(world, **overrides):
    return tool(
        world,
        "set_workflow_definition_enabled",
        **{
            "definition_id": str(world.definition.guid),
            "expected_revision": definition_revision(world.definition),
            "is_enabled": True,
            **overrides,
        },
    )


def test_activation_receipt_matches_fresh_review_and_unchanged_call_does_not_write(activation):
    assert TOOL in {row["name"] for row in rpc(activation, "tools/list").json()["result"]["tools"]}
    old = definition_revision(activation.definition)
    response = activate(activation)
    result = data(response)
    assert json.loads(response["content"][0]["text"]) == result
    assert result["changed"] and result["is_enabled"]
    assert result["definition_id"] == str(activation.definition.guid)
    review = data(tool(activation, "get_workflow_definition", definition_id=result["definition_id"]))[
        "definition"
    ]
    assert result["revision"] == review["revision"] != old
    # A lost response is recovered by reading the exact definition. Replaying
    # the old review cannot silently authorize a different revision.
    stale = activate(activation, expected_revision=old)
    assert stale["isError"] and stale["structuredContent"]["errors"][0]["field"] == "expected_revision"
    activation.definition.refresh_from_db()
    version = activation.definition.version
    unchanged = data(activate(activation))
    assert not unchanged["changed"] and unchanged["revision"] == result["revision"]
    activation.definition.refresh_from_db()
    assert activation.definition.version == version
    assert not WorkflowDefinitionStart.objects.exists()


@pytest.mark.parametrize("field", ["name", "stage", "child"])
def test_stale_definition_graph_is_refused(activation, field):
    if field == "child":
        child = WorkflowDefinition.objects.create(
            organization=activation.world.org,
            project=activation.world.medops_project,
            name="Child",
            slug="activation-child",
            is_enabled=True,
        )
        WorkflowStage.objects.create(definition=child, order=0, kind="checkpoint")
        activation.stage.kind = "workflow"
        activation.stage.workflow_ref = child.slug
        activation.stage.save()
    revision = definition_revision(activation.definition)
    if field == "name":
        activation.definition.name = "Changed after review"
        activation.definition.save()
    elif field == "stage":
        activation.stage.prompt = "Changed after review"
        activation.stage.save()
    else:
        child.description = "Changed after review"
        child.save()
    refused = activate(activation, expected_revision=revision)
    assert refused["isError"] and refused["structuredContent"]["errors"][0]["field"] == "expected_revision"
    activation.definition.refresh_from_db()
    assert not activation.definition.is_enabled


@pytest.mark.parametrize("missing", ["mcp:write", "workflow:write", "mcp:dispatch", "workflow:trigger"])
def test_activation_requires_write_and_dispatch_scopes(activation, missing):
    activation.token.scopes.remove(missing)
    activation.token.save(update_fields=["scopes"])
    result = activate(activation)
    assert result["isError"] and result["structuredContent"]["code"] == "permission_denied"
    activation.definition.refresh_from_db()
    assert not activation.definition.is_enabled


def test_current_trigger_role_is_required_and_disable_needs_no_dispatch(activation):
    assert data(activate(activation))["is_enabled"]
    activation.definition.refresh_from_db()
    activation.token.scopes = ["mcp:read", "mcp:write", "workflow:write"]
    activation.token.save(update_fields=["scopes"])
    disabled = data(activate(activation, is_enabled=False))
    assert disabled["changed"] and not disabled["is_enabled"]
    activation.definition.refresh_from_db()
    activation.token.scopes += ["mcp:dispatch", "workflow:trigger"]
    activation.token.save(update_fields=["scopes"])
    activation.binding.deleted_at = timezone.now()
    activation.binding.save()
    refused = activate(activation)
    assert refused["isError"] and refused["structuredContent"]["code"] == "permission_denied"


@pytest.mark.parametrize("owner", ["sibling", "foreign", "global"])
def test_team_token_cannot_expand_owned_definition_scope(activation, owner):
    activation.token.team = activation.world.medops
    activation.token.save(update_fields=["team"])
    bind_role(
        activation.user,
        permissions=[Permission.WORKFLOW_UPDATE, Permission.WORKFLOW_TRIGGER],
        kind="ORG",
        scope_id=activation.world.org.pk,
        slug=f"broad-{uuid4().hex}",
    )
    target = activation.definitions[{"sibling": 2, "foreign": 3, "global": 4}[owner]]
    result = activate(
        activation, definition_id=str(target.guid), expected_revision=definition_revision(target)
    )
    assert result["isError"] and result["structuredContent"]["code"] == "permission_denied"


def test_source_managed_definition_remains_owned_by_its_writer(activation):
    activation.definition.source_repo = "org/workflows"
    activation.definition.source_path = "workflow.toml"
    activation.definition.save()
    result = activate(activation)
    assert result["isError"] and result["structuredContent"]["errors"][0]["field"] == "source"
    activation.definition.refresh_from_db()
    assert not activation.definition.is_enabled


def test_nested_parent_restriction_is_native(activation):
    data(activate(activation))
    activation.definition.refresh_from_db()
    parent = WorkflowDefinition.objects.create(
        organization=activation.world.org,
        project=activation.world.medops_project,
        name="Parent",
        slug="activation-parent",
        is_enabled=True,
    )
    WorkflowStage.objects.create(
        definition=parent, order=0, kind="workflow", workflow_ref=activation.definition.slug
    )
    result = activate(activation, is_enabled=False)
    assert result["isError"] and result["structuredContent"]["errors"][0]["field"] == "is_enabled"
    activation.definition.refresh_from_db()
    assert activation.definition.is_enabled


@pytest.mark.parametrize(
    "args",
    [{"definition_id": "bad"}, {"expected_revision": "bad"}, {"is_enabled": "false"}, {"unexpected": True}],
)
def test_invalid_arguments_cannot_activate(activation, args):
    result = activate(activation, **args)
    assert result["isError"] and result["structuredContent"]["code"] == "invalid_arguments"
    activation.definition.refresh_from_db()
    assert not activation.definition.is_enabled


def test_workflow_feature_flag_hides_and_refuses_activation(activation, monkeypatch):
    monkeypatch.setenv("FEATURE_WORKFLOWS", "false")
    assert TOOL not in {row["name"] for row in rpc(activation, "tools/list").json()["result"]["tools"]}
    assert activate(activation)["structuredContent"]["code"] == "not_found"


@pytest.fixture
async def activation_engine():
    from temporalio.client import Client
    from temporalio.testing import WorkflowEnvironment

    # The time-skipping test server does not implement execution visibility.
    client = await Client.connect(
        os.environ.get("ASTROLIFT_TEST_TEMPORAL_ADDRESS") or os.environ["TEMPORAL_ADDRESS"],
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
    )
    async with WorkflowEnvironment.from_client(client) as env:
        yield env


async def test_activate_review_start_and_reconnect_to_real_execution(
    activation, activation_engine, monkeypatch, settings
):
    temporal_env = activation_engine
    async with await connect_real_engine(temporal_env, monkeypatch, settings):
        before = {(r.id, r.run_id) async for r in temporal_env.client.list_workflows()}
        old = await sync_to_async(reviewed_args)(activation)
        changed = data(await sync_to_async(activate)(activation))
        assert changed["changed"] and changed["is_enabled"]
        assert {(r.id, r.run_id) async for r in temporal_env.client.list_workflows()} == before
        stale = await sync_to_async(tool)(activation, "start_workflow_definition", **old)
        assert stale["isError"] and stale["structuredContent"]["data"] is None
        args = await sync_to_async(reviewed_args)(activation)
        started = data(await sync_to_async(tool)(activation, "start_workflow_definition", **args))["data"]
        handle = temporal_env.client.get_workflow_handle(
            started["temporal_workflow_id"], run_id=started["temporal_run_id"]
        )
        await asyncio.wait_for(handle.result(), 20)
        recovered = data(
            await sync_to_async(tool)(activation, "get_workflow_start", request_id=args["request_id"])
        )["start"]
        assert recovered == started
        observed = data(
            await sync_to_async(tool)(
                activation, "get_workflow_execution", execution_id=started["execution_id"]
            )
        )["execution"]
        assert observed["is_terminal"] and observed["status"] == "completed"
