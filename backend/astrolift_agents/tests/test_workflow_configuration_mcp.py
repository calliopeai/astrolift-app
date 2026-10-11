"""Native configured workflows through real bearer-authenticated MCP requests."""

import json
from uuid import uuid4

import pytest

from astrolift_agents.tests.test_workflow_mcp import data, rpc, tool
from astrolift_agents.tests.test_workflow_mcp import mcp as mcp_fixture
from astrolift_agents.tests.test_workflow_mcp import no_opensearch as no_opensearch
from astrolift_workflows.tests.schedule_server import schedule_server as schedule_server_fixture
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role
from workflows.manifest import create_definition_from_manifest, parse_workflow_manifest
from workflows.models import Workflow, WorkflowDefinitionStart, WorkflowInstance
from workflows.tests.test_manifest import ONE_GATE_TOML

mcp = mcp_fixture
schedule_server = schedule_server_fixture
pytestmark = pytest.mark.django_db
PERMISSIONS = [
    Permission.WORKFLOW_READ,
    Permission.WORKFLOW_CREATE,
    Permission.WORKFLOW_UPDATE,
    Permission.WORKFLOW_DELETE,
    Permission.WORKFLOW_TRIGGER,
]
WRITE_SCOPES = ["mcp:read", "mcp:write", "workflow:write"]


@pytest.fixture
def configured(mcp, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    mcp.token.scopes = WRITE_SCOPES.copy()
    mcp.token.team = mcp.world.medops
    mcp.token.save(update_fields=["scopes", "team"])
    bind_role(
        mcp.user,
        permissions=PERMISSIONS,
        kind="PROJECT",
        scope_id=mcp.world.medops_project.pk,
        slug=f"mcp-config-{uuid4().hex}",
    )
    mcp.definition = create_definition_from_manifest(
        parse_workflow_manifest(ONE_GATE_TOML), organization=mcp.world.org, project=mcp.world.medops_project
    )
    mcp.definition.is_enabled = True
    mcp.definition.save()
    return mcp


def create(mcp, **overrides):
    return tool(
        mcp,
        "create_workflow",
        **{"definition_id": str(mcp.definition.guid), "name": "Weekly research brief", **overrides},
    )


def dispatch(mcp):
    mcp.token.scopes = [*WRITE_SCOPES, "mcp:dispatch", "workflow:trigger"]
    mcp.token.save(update_fields=["scopes"])


def test_create_is_inactive_get_is_exact_and_edits_preserve_false_and_empty(configured):
    names = {r["name"] for r in rpc(configured, "tools/list").json()["result"]["tools"]}
    assert {f"astrolift_{verb}_workflow" for verb in ("get", "create", "update", "delete")} <= names
    created = data(create(configured, inputs={"audience": "team"}, description="Prepare a brief"))
    row = created["workflow"]
    assert created["configuration_saved"] and not row["is_enabled"]
    assert row["definition_guid"] == str(configured.definition.guid)
    assert created["schedule"]["observed_state"] == "not_requested"
    observed = data(tool(configured, "get_workflow", workflow_id=row["guid"]))["workflow"]
    assert observed == row
    updated = data(
        tool(
            configured,
            "update_workflow",
            workflow_id=row["guid"],
            expected_version=row["version"],
            inputs={},
            stage_bindings={},
            description="",
            is_enabled=False,
            schedule_cron="",
        )
    )["workflow"]
    assert updated["version"] > row["version"]
    assert updated["inputs"] == updated["stage_bindings"] == {}
    assert updated["description"] == "" and updated["schedule_cron"] is None
    assert not updated["is_enabled"] and updated["name"] == row["name"]
    removed = data(
        tool(configured, "delete_workflow", workflow_id=row["guid"], expected_version=updated["version"])
    )
    assert removed["configuration_saved"]
    assert removed["schedule"]["workflow_id"] == row["guid"]
    assert Workflow.objects.get(guid=row["guid"]).deleted_at is not None
    assert not WorkflowInstance.objects.exists() and not WorkflowDefinitionStart.objects.exists()


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_stale_version_does_not_write(configured, operation):
    row = data(create(configured))["workflow"]
    changed = data(
        tool(
            configured,
            "update_workflow",
            workflow_id=row["guid"],
            expected_version=row["version"],
            name="New brief",
        )
    )["workflow"]
    refused = tool(
        configured, f"{operation}_workflow", workflow_id=row["guid"], expected_version=row["version"]
    )
    payload = refused["structuredContent"]
    assert refused["isError"] and not payload["configuration_saved"]
    assert payload["errors"][0]["field"] == "expected_version"
    assert json.loads(refused["content"][0]["text"]) == payload
    current = Workflow.objects.get(guid=row["guid"])
    assert (
        current.version == changed["version"] and current.name == "New brief" and current.deleted_at is None
    )


@pytest.mark.parametrize("missing", ["mcp:write", "workflow:write"])
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_mutation_scopes_are_required(configured, missing, operation):
    row = data(create(configured))["workflow"]
    configured.token.scopes.remove(missing)
    configured.token.save(update_fields=["scopes"])
    names = {r["name"] for r in rpc(configured, "tools/list").json()["result"]["tools"]}
    assert f"astrolift_{operation}_workflow" not in names
    refused = (
        create(configured)
        if operation == "create"
        else tool(
            configured, f"{operation}_workflow", workflow_id=row["guid"], expected_version=row["version"]
        )
    )
    assert refused["isError"] and refused["structuredContent"]["code"] == "permission_denied"


@pytest.mark.parametrize("owner", ["sibling", "foreign", "global"])
def test_create_cannot_expand_token_owner_even_with_org_role(configured, owner):
    bind_role(
        configured.user,
        permissions=PERMISSIONS,
        kind="ORG",
        scope_id=configured.world.org.pk,
        slug=f"broad-{uuid4().hex}",
    )
    definition = configured.definitions[{"sibling": 2, "foreign": 3, "global": 4}[owner]]
    before = Workflow.objects.count()
    refused = create(configured, definition_id=str(definition.guid))
    assert refused["isError"] and refused["structuredContent"]["code"] == "permission_denied"
    assert Workflow.objects.count() == before


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
@pytest.mark.parametrize("owner", ["sibling", "foreign"])
def test_exact_operations_cannot_expand_token_owner(configured, operation, owner):
    target = configured.configured[2 if owner == "sibling" else 3]
    bind_role(
        configured.user,
        permissions=PERMISSIONS,
        kind="ORG",
        scope_id=configured.world.org.pk,
        slug=f"broad-{uuid4().hex}",
    )
    args = {"workflow_id": str(target.guid)}
    if operation != "get":
        args["expected_version"] = target.version
    refused = tool(configured, f"{operation}_workflow", **args)
    assert refused["isError"] and refused["structuredContent"]["code"] == "permission_denied"
    target.refresh_from_db()
    assert target.deleted_at is None


@pytest.mark.parametrize(
    "args",
    [
        {"expected_version": True},
        {"expected_version": 0},
        {"expected_version": 2147483648},
        {"workflow_id": "bad"},
        {"is_enabled": "false"},
        {"inputs": []},
        {"trigger_kind": "event"},
        {"unknown": True},
    ],
)
def test_invalid_arguments_do_not_mutate(configured, args):
    row = data(create(configured))["workflow"]
    refused = tool(
        configured,
        "update_workflow",
        **{"workflow_id": row["guid"], "expected_version": row["version"], **args},
    )
    assert refused["isError"] and refused["structuredContent"]["code"] == "invalid_arguments"
    assert Workflow.objects.get(guid=row["guid"]).version == row["version"]


def test_version_is_required_and_feature_gate_hides_all_configuration_tools(configured, monkeypatch):
    row = data(create(configured))["workflow"]
    assert (
        tool(configured, "delete_workflow", workflow_id=row["guid"])["structuredContent"]["code"]
        == "invalid_arguments"
    )
    monkeypatch.setenv("FEATURE_WORKFLOWS", "false")
    names = {r["name"] for r in rpc(configured, "tools/list").json()["result"]["tools"]}
    assert not {f"astrolift_{verb}_workflow" for verb in ("get", "create", "update", "delete")} & names
    assert create(configured)["structuredContent"]["code"] == "not_found"


@pytest.mark.parametrize("missing", ["mcp:dispatch", "workflow:trigger"])
def test_active_create_checks_dispatch_before_save(configured, missing):
    dispatch(configured)
    configured.token.scopes.remove(missing)
    configured.token.save(update_fields=["scopes"])
    before = Workflow.objects.count()
    refused = create(configured, is_enabled=True, trigger_kind="schedule", schedule_cron="0 0 1 1 *")
    assert refused["isError"] and refused["structuredContent"]["code"] == "permission_denied"
    assert Workflow.objects.count() == before


def test_native_binding_validation_and_repoint_authority_are_preserved(configured):
    row = data(create(configured))["workflow"]
    bad = tool(
        configured,
        "update_workflow",
        workflow_id=row["guid"],
        expected_version=row["version"],
        stage_bindings={"0": {"agent_workload_id": str(uuid4())}},
    )
    assert bad["isError"] and not bad["structuredContent"]["configuration_saved"]
    assert bad["structuredContent"]["errors"][0]["field"] == "stage_bindings"
    refused = tool(
        configured,
        "update_workflow",
        workflow_id=row["guid"],
        expected_version=row["version"],
        definition_id=str(configured.definitions[2].guid),
    )
    assert refused["isError"] and refused["structuredContent"]["code"] == "permission_denied"
    current = Workflow.objects.get(guid=row["guid"])
    assert current.version == row["version"] and current.definition_id == configured.definition.pk


@pytest.mark.django_db(transaction=True)
def test_post_save_activation_refusal_preserves_saved_receipt(configured, schedule_server, monkeypatch):
    from astrolift_workflows.schema import mutations

    dispatch(configured)
    original = mutations._configured_schedule_result

    def revoked(workflow, permission, info):
        schedule_server.track(workflow)
        info.context.request._api_token.scopes.remove("mcp:dispatch")
        return original(workflow, permission, info)

    monkeypatch.setattr(mutations, "_configured_schedule_result", revoked)
    failed = create(configured, is_enabled=True, trigger_kind="schedule", schedule_cron="0 0 1 1 *")
    receipt = failed["structuredContent"]
    assert failed["isError"] and receipt["configuration_saved"]
    assert receipt["schedule"]["error_code"] == "permission_denied"
    row = Workflow.objects.get(guid=receipt["workflow"]["guid"])
    assert row.is_enabled and schedule_server.describe(row) is None


@pytest.mark.django_db(transaction=True)
def test_real_schedule_create_edit_pause_delete_and_partial_recovery(configured, schedule_server, settings):
    dispatch(configured)
    result = data(create(configured, is_enabled=True, trigger_kind="schedule", schedule_cron="0 0 1 1 *"))
    row = Workflow.objects.get(guid=result["workflow"]["guid"])
    schedule_server.track(row)
    assert result["schedule"]["confirmed"] and result["schedule"]["observed_state"] == "active"
    assert schedule_server.describe(row) is not None
    changed = data(
        tool(
            configured,
            "update_workflow",
            workflow_id=str(row.guid),
            expected_version=row.version,
            schedule_cron="0 1 1 1 *",
            inputs={"audience": "board"},
        )
    )
    assert changed["schedule"]["confirmed"]
    assert changed["schedule"]["engine_created_at"] == result["schedule"]["engine_created_at"]
    row.refresh_from_db()
    # An input-only edit still changes future dispatches; no is_enabled argument is needed.
    configured.token.scopes = [*WRITE_SCOPES, "workflow:trigger"]
    configured.token.save(update_fields=["scopes"])
    denied = tool(
        configured,
        "update_workflow",
        workflow_id=str(row.guid),
        expected_version=row.version,
        inputs={"audience": "unreviewed"},
    )
    assert denied["isError"] and denied["structuredContent"]["code"] == "permission_denied"
    row.refresh_from_db()
    assert row.inputs == {"audience": "board"}
    # Pausing needs write authority but no dispatch scope.
    paused = data(
        tool(
            configured,
            "update_workflow",
            workflow_id=str(row.guid),
            expected_version=row.version,
            is_enabled=False,
        )
    )
    assert paused["schedule"]["confirmed"] and schedule_server.describe(row) is None
    dispatch(configured)
    row.refresh_from_db()
    assert data(
        tool(
            configured,
            "update_workflow",
            workflow_id=str(row.guid),
            expected_version=row.version,
            is_enabled=True,
        )
    )["schedule"]["confirmed"]
    row.refresh_from_db()
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    failed = tool(configured, "delete_workflow", workflow_id=str(row.guid), expected_version=row.version)
    receipt = failed["structuredContent"]
    assert failed["isError"] and receipt["configuration_saved"]
    assert receipt == json.loads(failed["content"][0]["text"])
    assert receipt["schedule"]["workflow_id"] == str(row.guid)
    assert receipt["schedule"]["error_code"] == "engine_disabled"
    row.refresh_from_db()
    assert row.deleted_at is not None and schedule_server.describe(row) is not None
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    recovered = data(
        tool(
            configured,
            "reconcile_workflow_schedule",
            workflow_id=str(row.guid),
            expected_version=row.version,
            expected_active=False,
        )
    )
    assert recovered["schedule"]["confirmed"] and schedule_server.describe(row) is None
