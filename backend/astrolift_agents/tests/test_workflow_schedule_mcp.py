"""Schedule recovery through bearer-authenticated MCP preserves native authority."""

import json

import pytest
from django.utils import timezone

from astrolift_agents.tests.test_fleet_scopes_1745 import no_opensearch as no_opensearch
from astrolift_agents.tests.test_workflow_mcp import data, rpc, tool
from astrolift_agents.tests.test_workflow_mcp import mcp as mcp_fixture
from astrolift_identity.api_tokens import (
    SCOPE_MCP_DISPATCH,
    SCOPE_MCP_READ,
    SCOPE_MCP_WRITE,
    SCOPE_WORKFLOW_TRIGGER,
    SCOPE_WORKFLOW_WRITE,
)
from astrolift_workflows.tests.schedule_server import schedule_server as schedule_server_fixture
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role

schedule_server = schedule_server_fixture
mcp = mcp_fixture

pytestmark = pytest.mark.django_db


@pytest.fixture
def scheduled(mcp, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    mcp.token.scopes = [
        SCOPE_MCP_READ,
        SCOPE_MCP_WRITE,
        SCOPE_WORKFLOW_WRITE,
        SCOPE_MCP_DISPATCH,
        SCOPE_WORKFLOW_TRIGGER,
    ]
    mcp.token.save(update_fields=["scopes"])
    bind_role(
        mcp.user,
        permissions=[Permission.WORKFLOW_READ, Permission.WORKFLOW_UPDATE, Permission.WORKFLOW_TRIGGER],
        kind="PROJECT",
        scope_id=mcp.world.medops_project.pk,
        slug="mcp-schedule-role",
    )
    mcp.row = mcp.configured[0]
    mcp.row.definition.is_enabled = True
    mcp.row.definition.save()
    # These discovery fixtures intentionally have unbound dispatch stages.
    # Keep native configuration validation intact; recovery operates on an existing row.
    type(mcp.row).objects.filter(pk=mcp.row.pk).update(
        is_enabled=True,
        trigger_kind="schedule",
        schedule_cron="0 0 1 1 *",
        schedule_managed=True,
    )
    mcp.row.refresh_from_db()
    return mcp


def args(scheduled, **overrides):
    return {
        "workflow_id": str(scheduled.row.guid),
        "expected_version": scheduled.row.version,
        "expected_active": True,
        **overrides,
    }


def test_observation_and_failed_recovery_preserve_identity(scheduled):
    observed = data(tool(scheduled, "get_workflow_schedule", workflow_id=str(scheduled.row.guid)))["schedule"]
    assert observed["error_code"] == "engine_disabled" and not observed["confirmed"]
    failed = tool(scheduled, "reconcile_workflow_schedule", **args(scheduled))
    assert failed["isError"]
    assert failed["structuredContent"] == json.loads(failed["content"][0]["text"])
    assert failed["structuredContent"]["schedule"]["workflow_id"] == str(scheduled.row.guid)


@pytest.mark.parametrize(
    "missing_scope", [SCOPE_MCP_DISPATCH, SCOPE_WORKFLOW_TRIGGER, SCOPE_MCP_WRITE, SCOPE_WORKFLOW_WRITE]
)
def test_activation_requires_both_write_and_dispatch_scopes(scheduled, missing_scope):
    scheduled.token.scopes.remove(missing_scope)
    scheduled.token.save(update_fields=["scopes"])
    refused = tool(scheduled, "reconcile_workflow_schedule", **args(scheduled))
    assert refused["isError"] and refused["structuredContent"]["code"] == "permission_denied"


def test_false_activation_assertion_cannot_bypass_dispatch_scope(scheduled, schedule_server):
    scheduled.token.scopes = [SCOPE_MCP_WRITE, SCOPE_WORKFLOW_WRITE, SCOPE_WORKFLOW_TRIGGER]
    scheduled.token.save(update_fields=["scopes"])
    schedule_server.track(scheduled.row)
    refused = tool(scheduled, "reconcile_workflow_schedule", **args(scheduled, expected_active=False))
    assert refused["isError"]
    assert refused["structuredContent"]["schedule"]["error_code"] == "stale_configuration"
    assert schedule_server.describe(scheduled.row) is None


def test_soft_deleted_cleanup_requires_delete_permission(scheduled, schedule_server):
    schedule_server.track(scheduled.row)
    scheduled.token.scopes = [SCOPE_MCP_READ, SCOPE_MCP_WRITE, SCOPE_WORKFLOW_WRITE]
    scheduled.token.save(update_fields=["scopes"])
    type(scheduled.row).objects.filter(pk=scheduled.row.pk).update(deleted_at=timezone.now())
    refused = tool(scheduled, "reconcile_workflow_schedule", **args(scheduled, expected_active=False))
    assert (
        refused["isError"] and refused["structuredContent"]["schedule"]["error_code"] == "permission_denied"
    )
    bind_role(
        scheduled.user,
        permissions=[Permission.WORKFLOW_DELETE],
        kind="PROJECT",
        scope_id=scheduled.world.medops_project.pk,
        slug="mcp-schedule-cleanup",
    )
    result = data(tool(scheduled, "reconcile_workflow_schedule", **args(scheduled, expected_active=False)))
    assert result["schedule"]["confirmed"] and result["schedule"]["observed_state"] == "missing"


def test_readonly_token_can_inspect_but_not_reconcile(scheduled):
    scheduled.token.scopes = [SCOPE_MCP_READ]
    scheduled.token.save(update_fields=["scopes"])
    names = {row["name"] for row in rpc(scheduled, "tools/list").json()["result"]["tools"]}
    assert "astrolift_get_workflow_schedule" in names
    assert "astrolift_reconcile_workflow_schedule" not in names
    assert data(tool(scheduled, "get_workflow_schedule", workflow_id=str(scheduled.row.guid)))["schedule"]


def test_schedule_tools_honor_workflows_feature_flag(scheduled, monkeypatch):
    monkeypatch.setenv("FEATURE_WORKFLOWS", "false")
    names = {row["name"] for row in rpc(scheduled, "tools/list").json()["result"]["tools"]}
    assert "astrolift_get_workflow_schedule" not in names
    refused = tool(scheduled, "get_workflow_schedule", workflow_id=str(scheduled.row.guid))
    assert refused["isError"] and refused["structuredContent"]["code"] == "not_found"
