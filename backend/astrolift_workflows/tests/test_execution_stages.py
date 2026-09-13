from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_graphql.pagination import decode_cursor, encode_cursor
from astrolift_operations.models import WorkflowRun
from astrolift_workflows import execution_controls as controls
from astrolift_workflows.tests.test_execution_surface import (  # noqa: F401
    api,  # noqa: F811
    owned_execution,  # noqa: F811
)
from astrolift_workflows.tests.test_workflow_stage_activities import (  # noqa: F401
    agent_workload,
    definition,
    org,
    run,
)
from core.permissions import Permission, PermissionScope, ScopeKind
from workflows.models import WorkflowStageExecution

pytestmark = pytest.mark.django_db
QUERY = """query($id: ID!, $limit: Int! = 100, $after: String) {
 workflowExecutionStages(executionId: $id, limit: $limit, after: $after) {
  executionGuid recordId organizationGuid temporalWorkflowId temporalRunId
  stages { nextCursor totalCount items {
   guid executionId stageGuid stageOrder stageKind stageRole stageApprovers status attemptNumber
   humanGateState humanGateNote startedAt endedAt errorMessage agentRunGuid
   childWorkflowRunGuid childWorkflowDefinitionSlug childWorkflowStatus
  } }
 }
}"""


def stages(execution, count):
    gate = execution.workflow_definition.stages.get(order=1)
    return WorkflowStageExecution.objects.bulk_create(
        [
            WorkflowStageExecution(
                workflow_run=execution,
                stage=gate,
                attempt_number=index + 1,
                status="failed",
                error_message=f"Attempt {index + 1}",
            )
            for index in range(count)
        ]
    )


@pytest.mark.parametrize("identifier", ["pk", "guid"])
def test_exact_stage_walk_keeps_all_attempts_and_excludes_newer_runs(
    owned_execution,  # noqa: F811
    api,  # noqa: F811
    monkeypatch,
    identifier,  # noqa: F811
):
    monkeypatch.setattr(
        controls, "describe_workflow_instance", lambda *a, **kw: pytest.fail("unexpected Temporal read")
    )
    records = stages(owned_execution, 205)
    WorkflowStageExecution.objects.filter(pk__in=[row.pk for row in records]).update(
        created_at=timezone.now()
    )
    neighbors = WorkflowRun.objects.bulk_create(
        [
            WorkflowRun(
                organization=owned_execution.organization,
                workflow_kind=controls.WORKFLOW_KIND,
                workflow_id=owned_execution.workflow_id,
                run_id=f"newer-incarnation-{index}",
                workflow_definition=owned_execution.workflow_definition,
            )
            for index in range(205)
        ]
    )
    stages(neighbors[-1], 1)
    seen, after, sizes = [], None, []
    while True:
        result = api(QUERY, {"id": str(getattr(owned_execution, identifier)), "after": after})
        assert not result.errors
        reply = result.data["workflowExecutionStages"]
        assert reply["recordId"] == str(owned_execution.pk)
        assert reply["executionGuid"] == str(owned_execution.guid)
        assert reply["organizationGuid"] == str(owned_execution.organization.guid)
        assert reply["temporalRunId"] == owned_execution.run_id
        page = reply["stages"]
        assert page["totalCount"] is None
        seen.extend(row["guid"] for row in page["items"])
        sizes.append(len(page["items"]))
        after = page["nextCursor"]
        if not after:
            break
        if len(sizes) == 1:
            stages(owned_execution, 1)
        assert len(sizes) < 4
    assert sizes == [100, 100, 5]
    assert seen == sorted((str(row.guid) for row in records), reverse=True)


def test_stage_metadata_survives_deleted_definition_stage_and_keeps_child_links(
    owned_execution,  # noqa: F811
    api,  # noqa: F811
):
    row = stages(owned_execution, 1)[0]
    gate = row.stage
    gate.role, gate.approvers, gate.deleted_at = "Review", ["maintainers"], timezone.now()
    gate.save()
    row.status, row.output = "completed", {"human_gate": {"decision": "approved", "note": "Reviewed"}}
    row.started_at, row.ended_at = timezone.now(), timezone.now()
    row.save()
    child = WorkflowRun.objects.create(
        organization=owned_execution.organization,
        workflow_kind=controls.WORKFLOW_KIND,
        workflow_id="child-workflow",
        run_id="child-execution",
        workflow_definition=owned_execution.workflow_definition,
        parent_stage_execution=row,
        status="completed",
    )
    result = api(QUERY)
    assert not result.errors
    stage = result.data["workflowExecutionStages"]["stages"]["items"][0]
    assert stage["executionId"] == str(row.pk)
    assert stage["stageGuid"] == str(gate.guid)
    assert stage["stageRole"] == "Review"
    assert stage["stageApprovers"] == ["maintainers"]
    assert stage["humanGateState"] == "approved"
    assert stage["humanGateNote"] == "Reviewed"
    assert stage["startedAt"] and stage["endedAt"]
    assert stage["childWorkflowRunGuid"] == str(child.guid)
    assert stage["childWorkflowDefinitionSlug"] == owned_execution.workflow_definition.slug
    assert stage["childWorkflowStatus"] == "completed"
    assert stage["agentRunGuid"] is None


@pytest.mark.parametrize("invisible", ["foreign", "deleted", "deleted_org", "non_definition", "missing"])
def test_stage_lookup_distinguishes_empty_execution_from_invisible_execution(
    owned_execution,  # noqa: F811
    api,  # noqa: F811
    invisible,  # noqa: F811
):
    from astrolift_identity.models import Organization

    assert api(QUERY).data["workflowExecutionStages"]["stages"]["items"] == []
    stages(owned_execution, 1)
    caller = owned_execution.organization_id
    identifier = str(owned_execution.pk)
    if invisible == "foreign":
        owned_execution.organization = Organization.objects.create(name="Other", slug="other-stage-org")
    elif invisible == "deleted":
        owned_execution.deleted_at = timezone.now()
    elif invisible == "deleted_org":
        owned_execution.organization.deleted_at = timezone.now()
        owned_execution.organization.save()
    elif invisible == "non_definition":
        owned_execution.workflow_kind = "DeployAppWorkflow"
    else:
        identifier = "9223372036854775807"
    owned_execution.save()
    result = api(QUERY, {"id": identifier}, organization_id=caller)
    assert not result.errors
    assert result.data["workflowExecutionStages"] is None


def test_stage_read_requires_definition_project_permission(
    owned_execution,  # noqa: F811
    api,  # noqa: F811
    permission_resolver,  # noqa: F811
):
    project = owned_execution.workflow_definition.stages.first().agent_definition.registered_app.project
    owned_execution.workflow_definition.project = project
    owned_execution.workflow_definition.save()
    permission_resolver.deny(
        Permission.WORKFLOW_READ, scope=PermissionScope(kind=ScopeKind.PROJECT, id=project.pk)
    )
    result = api(QUERY)
    assert result.errors and Permission.WORKFLOW_READ.value in result.errors[0].message


@pytest.mark.parametrize("bad", ["", "garbage", "x" * 4097, "scope", "time", "naive", "guid", "reused"])
def test_stage_cursor_cannot_restart_or_change_execution(
    owned_execution,  # noqa: F811
    api,  # noqa: F811
    bad,  # noqa: F811
):
    stages(owned_execution, 2)
    variables = {"id": str(owned_execution.pk), "limit": 1}
    cursor = api(QUERY, variables).data["workflowExecutionStages"]["stages"]["nextCursor"]
    parts = list(decode_cursor(cursor, arity=3))
    if bad == "scope":
        parts[0] = "another-execution"
    elif bad == "time":
        parts[1] = "not-a-time"
    elif bad == "naive":
        parts[1] = "2026-09-12T12:00:00"
    elif bad == "guid":
        parts[2] = "not-a-guid"
    elif bad == "reused":
        owned_execution.run_id = "new-incarnation"
        owned_execution.save()
    else:
        cursor = bad
    if bad in {"scope", "time", "naive", "guid"}:
        cursor = encode_cursor(*parts)
    result = api(QUERY, {**variables, "after": cursor})
    assert result.errors and "cursor" in result.errors[0].message


def test_stage_page_query_count_is_constant_and_excludes_deleted_executions(
    owned_execution,  # noqa: F811
    api,  # noqa: F811
):
    rows = stages(owned_execution, 100)
    WorkflowStageExecution.objects.filter(pk=rows[0].pk).update(deleted_at=timezone.now())
    counts = []
    for limit in [1, 100]:
        with CaptureQueriesContext(connection) as queries:
            result = api(QUERY, {"id": str(owned_execution.pk), "limit": limit})
        assert not result.errors
        assert len(result.data["workflowExecutionStages"]["stages"]["items"]) == min(limit, 99)
        counts.append(len(queries))
    assert counts[0] == counts[1]
