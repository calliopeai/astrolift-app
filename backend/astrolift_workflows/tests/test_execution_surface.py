from __future__ import annotations

from types import SimpleNamespace

import pytest
import strawberry
from django.utils import timezone

from astrolift_operations.models import WorkflowRun
from astrolift_workflows import execution_controls as controls
from astrolift_workflows.schema.mutations import WorkflowsMutation
from astrolift_workflows.schema.queries import WorkflowsQuery
from astrolift_workflows.tests.test_workflow_stage_activities import (  # noqa: F401
    _dispatch_agent_stage,
    _fake_spawner,
    agent_workload,
    definition,
    org,
    run,
)
from core.permissions import Permission, PermissionScope, ScopeKind
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
schema = strawberry.Schema(query=WorkflowsQuery, mutation=WorkflowsMutation)
FIELDS = """guid recordId organizationGuid definitionSlug status temporalWorkflowId
 temporalRunId startedAt endedAt isTerminal failure taskCleanup observationError"""
QUERY = "query($id: ID!) { workflowExecution(executionId: $id) { " + FIELDS + " } }"
MUTATION = (
    """mutation($id: ID!, $workflow: String!, $run: String!, $action: String!, $reason: String!) {
 controlWorkflowExecution(executionId: $id, workflowId: $workflow, runId: $run, action: $action, reason: $reason) {
  ok requested errors { field messages } execution { """
    + FIELDS
    + " } } }"
)


@pytest.fixture
def owned_execution(run, definition):  # noqa: F811
    run.workflow_definition = definition
    run.run_id = "original-execution"
    run.save(update_fields=["workflow_definition", "run_id", "updated_at", "version"])
    return run


@pytest.fixture
def observation(owned_execution, monkeypatch):
    observed = {
        "workflow_id": owned_execution.workflow_id,
        "run_id": owned_execution.run_id,
        "workflow_type": controls.WORKFLOW_KIND,
        "status": "RUNNING",
        "closed_at": "",
    }
    monkeypatch.setattr(controls, "describe_workflow_instance", lambda *a, **kw: dict(observed))
    return observed


@pytest.fixture
def api(owned_execution, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)

    def execute(document=QUERY, variables=None, *, organization_id=None):
        with tenant_context(
            TenantContext(organization_id=organization_id or owned_execution.organization_id)
        ):
            return schema.execute_sync(
                document,
                variable_values=variables or {"id": str(owned_execution.pk)},
                context_value=SimpleNamespace(user=None, request=None),
            )

    return execute


def control_variables(execution, action="cancel", **overrides):
    return {
        "id": str(execution.pk),
        "workflow": execution.workflow_id,
        "run": execution.run_id,
        "action": action,
        "reason": "",
        **overrides,
    }


@pytest.mark.parametrize("identifier", ["pk", "guid"])
def test_exact_execution_remains_accessible_beyond_recent_run_limits(
    owned_execution, observation, api, identifier
):
    WorkflowRun.objects.bulk_create(
        [
            WorkflowRun(
                organization_id=owned_execution.organization_id,
                workflow_kind=controls.WORKFLOW_KIND,
                workflow_id=f"newer-{index}",
                run_id=f"newer-execution-{index}",
            )
            for index in range(205)
        ]
    )
    result = api(variables={"id": str(getattr(owned_execution, identifier))})
    assert not result.errors
    row = result.data["workflowExecution"]
    assert row["recordId"] == str(owned_execution.pk)
    assert row["guid"] == str(owned_execution.guid)
    assert row["temporalRunId"] == owned_execution.run_id
    assert row["taskCleanup"]["status"] == "not_requested"


@pytest.mark.parametrize("identifier", ["0", "-1", "9223372036854775808", "9" * 100, "invalid", "１２"])
def test_malformed_execution_identifiers_do_not_reach_temporal(api, monkeypatch, identifier):
    monkeypatch.setattr(
        controls, "describe_workflow_instance", lambda *a, **kw: pytest.fail("unexpected read")
    )
    result = api(variables={"id": identifier})
    assert not result.errors
    assert result.data["workflowExecution"] is None


@pytest.mark.parametrize("invisible", ["foreign", "deleted", "non_definition"])
def test_execution_lookup_and_control_do_not_expose_invisible_records(
    owned_execution, api, monkeypatch, invisible
):
    from astrolift_identity.models import Organization

    caller = owned_execution.organization_id
    if invisible == "foreign":
        owned_execution.organization = Organization.objects.create(name="Other", slug="other-execution-org")
    elif invisible == "deleted":
        owned_execution.deleted_at = timezone.now()
    else:
        owned_execution.workflow_kind = "DeployAppWorkflow"
    owned_execution.save()
    monkeypatch.setattr(
        controls, "describe_workflow_instance", lambda *a, **kw: pytest.fail("unexpected read")
    )
    assert api(organization_id=caller).data["workflowExecution"] is None
    result = api(MUTATION, control_variables(owned_execution), organization_id=caller)
    assert not result.errors
    assert result.data["controlWorkflowExecution"]["requested"] is False
    assert result.data["controlWorkflowExecution"]["errors"][0]["messages"] == ["Execution not found"]


@pytest.mark.parametrize("operation", ["read", "control"])
def test_execution_operations_enforce_project_permissions(
    owned_execution, api, permission_resolver, monkeypatch, operation
):
    project = owned_execution.workflow_definition.stages.first().agent_definition.registered_app.project
    owned_execution.workflow_definition.project = project
    owned_execution.workflow_definition.save(update_fields=["project", "updated_at", "version"])
    permission = Permission.WORKFLOW_READ if operation == "read" else Permission.WORKFLOW_TRIGGER
    permission_resolver.deny(permission, scope=PermissionScope(kind=ScopeKind.PROJECT, id=project.pk))
    monkeypatch.setattr(
        controls, "describe_workflow_instance", lambda *a, **kw: pytest.fail("unexpected read")
    )
    result = api() if operation == "read" else api(MUTATION, control_variables(owned_execution))
    assert result.errors
    assert permission.value in result.errors[0].message


@pytest.mark.parametrize("problem", ["unavailable", "workflow", "run", "kind", "status", "close_time"])
def test_unverified_observations_retain_recorded_state_and_refuse_cleanup(
    owned_execution, api, observation, monkeypatch, problem
):
    owned_execution.status, owned_execution.ended_at = "cancelled", timezone.now()
    owned_execution.save(update_fields=["status", "ended_at", "updated_at", "version"])
    version = owned_execution.version
    if problem == "unavailable":
        monkeypatch.setattr(controls, "describe_workflow_instance", lambda *a, **kw: None)
    else:
        key, value = {
            "workflow": ("workflow_id", "different-workflow"),
            "run": ("run_id", "different-execution"),
            "kind": ("workflow_type", "DeployAppWorkflow"),
            "status": ("status", "CONTINUED_AS_NEW"),
            "close_time": ("status", "CANCELED"),
        }[problem]
        observation[key] = value
    result = api()
    assert not result.errors
    assert result.data["workflowExecution"]["status"] == "cancelled"
    assert result.data["workflowExecution"]["observationError"]
    result = api(MUTATION, control_variables(owned_execution, "cleanup"))
    assert not result.errors
    assert not result.data["controlWorkflowExecution"]["ok"]
    owned_execution.refresh_from_db()
    assert owned_execution.version == version


@pytest.mark.parametrize("action", ["cancel", "terminate"])
def test_control_pins_both_temporal_identifiers_without_claiming_completion(
    owned_execution, api, observation, monkeypatch, action
):
    calls = []
    method = "cancel_workflow" if action == "cancel" else "terminate_workflow"
    monkeypatch.setattr(controls, method, lambda *a, **kw: calls.append((a, kw)) or True)
    result = api(
        MUTATION, control_variables(owned_execution, action, reason="stuck" if action == "terminate" else "")
    )
    assert not result.errors
    reply = result.data["controlWorkflowExecution"]
    assert reply["ok"] and reply["requested"]
    assert reply["execution"]["isTerminal"] is False
    assert calls == [
        (
            (owned_execution.workflow_id,) + (("stuck",) if action == "terminate" else ()),
            {"run_id": owned_execution.run_id},
        )
    ]


@pytest.mark.parametrize(
    "mismatch",
    [
        {"run": "other"},
        {"workflow": "other"},
        {"run": ""},
        {"action": "other"},
        {"action": "terminate"},
        {"reason": "unexpected"},
    ],
)
def test_invalid_control_is_rejected_before_external_io(owned_execution, api, monkeypatch, mismatch):
    monkeypatch.setattr(
        controls, "describe_workflow_instance", lambda *a, **kw: pytest.fail("unexpected read")
    )
    result = api(MUTATION, control_variables(owned_execution, **mismatch))
    assert not result.errors
    assert not result.data["controlWorkflowExecution"]["requested"]


def test_cleanup_requires_authoritative_closure_even_when_mirror_is_terminal(
    owned_execution, api, observation
):
    owned_execution.status, owned_execution.ended_at = "cancelled", timezone.now()
    owned_execution.save(update_fields=["status", "ended_at", "updated_at", "version"])
    result = api()
    assert result.data["workflowExecution"]["status"] == "running"
    assert not result.data["workflowExecution"]["isTerminal"]
    result = api(MUTATION, control_variables(owned_execution, "cleanup"))
    assert not result.data["controlWorkflowExecution"]["ok"]
    assert "still running" in result.data["controlWorkflowExecution"]["errors"][0]["messages"][0]


def test_cleanup_progress_remains_visible_and_retryable_after_execution_closes(
    owned_execution, api, observation, monkeypatch
):
    from astrolift_dispatch.spawners import registry
    from astrolift_workflows.tests.test_workflow_stage_activities import _FakeSpawner

    _, task = _dispatch_agent_stage(owned_execution, owned_execution.workflow_definition)
    observation.update(status="CANCELED", closed_at=timezone.now().isoformat())
    deleted = False

    class PendingSpawner(_FakeSpawner):
        def confirm_stopped(self, external_id):
            return deleted

    monkeypatch.setattr(registry, "get_spawner", lambda *a, **kw: PendingSpawner())
    row = api().data["workflowExecution"]
    assert row["isTerminal"]
    assert row["taskCleanup"]["status"] == "pending"
    assert row["taskCleanup"]["retryable"]
    reply = api(MUTATION, control_variables(owned_execution, "cleanup")).data["controlWorkflowExecution"]
    assert reply["ok"] and reply["execution"]["taskCleanup"]["status"] == "pending"
    task.refresh_from_db()
    assert task.status == "running"
    deleted = True
    reply = api(MUTATION, control_variables(owned_execution, "cleanup")).data["controlWorkflowExecution"]
    assert reply["ok"] and reply["execution"]["taskCleanup"]["status"] == "completed"
    assert not reply["execution"]["taskCleanup"]["retryable"]
    task.refresh_from_db()
    assert task.status == "cancelled"


@pytest.mark.parametrize("action", ["cleanup", "cancel", "terminate"])
def test_control_refuses_identity_changed_during_observation(
    owned_execution, api, observation, monkeypatch, action
):
    def describe(*a, **kw):
        WorkflowRun.objects.filter(pk=owned_execution.pk).update(run_id="replacement-execution")
        return {
            **observation,
            "status": "CANCELED" if action == "cleanup" else "RUNNING",
            "closed_at": timezone.now().isoformat(),
        }

    monkeypatch.setattr(controls, "describe_workflow_instance", describe)
    monkeypatch.setattr(controls, "cancel_workflow", lambda *a, **kw: pytest.fail("unexpected cancel"))
    monkeypatch.setattr(controls, "terminate_workflow", lambda *a, **kw: pytest.fail("unexpected terminate"))
    result = api(
        MUTATION, control_variables(owned_execution, action, reason="stuck" if action == "terminate" else "")
    )
    assert not result.errors
    assert not result.data["controlWorkflowExecution"]["ok"]
    owned_execution.refresh_from_db()
    assert owned_execution.status == "running"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action", ["cancel", "terminate"])
async def test_graphql_controls_exact_temporal_execution_and_preserves_reused_neighbor(
    owned_execution, permission_resolver, temporal_env, monkeypatch, action
):
    import asyncio

    from asgiref.sync import sync_to_async
    from temporalio.client import WorkflowFailureError

    from astrolift_workflows import client
    from astrolift_workflows.tests.test_workflow_run_reconcile import HoldingDefinition
    from core.testing.temporal import temporal_worker

    permission_resolver.grant(Permission.WORKFLOW_READ)
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)

    async def get_client():
        return temporal_env.client

    monkeypatch.setattr(client, "_get_client_async", get_client)
    monkeypatch.setattr(client, "_temporal_enabled", lambda: True)

    @sync_to_async
    def execute(execution, operation):
        with tenant_context(TenantContext(organization_id=execution.organization_id)):
            return schema.execute_sync(
                MUTATION,
                variable_values=control_variables(
                    execution, operation, reason="stuck" if operation == "terminate" else ""
                ),
                context_value=SimpleNamespace(user=None, request=None),
            )

    async with temporal_worker(temporal_env, workflows=[HoldingDefinition]):
        original = await temporal_env.client.start_workflow(
            HoldingDefinition.run, id=owned_execution.workflow_id, task_queue="astrolift-test"
        )
        owned_execution.run_id = original.first_execution_run_id
        await sync_to_async(owned_execution.save)(update_fields=["run_id", "updated_at", "version"])
        await original.signal(HoldingDefinition.finish)
        await asyncio.wait_for(original.result(), 10)
        neighbor = await temporal_env.client.start_workflow(
            HoldingDefinition.run, id=owned_execution.workflow_id, task_queue="astrolift-test"
        )
        other = await sync_to_async(WorkflowRun.objects.create)(
            organization_id=owned_execution.organization_id,
            workflow_kind=controls.WORKFLOW_KIND,
            workflow_id=neighbor.id,
            run_id=neighbor.first_execution_run_id,
            workflow_definition_id=owned_execution.workflow_definition_id,
        )
        try:
            result = await execute(owned_execution, action)
            assert not result.errors
            reply = result.data["controlWorkflowExecution"]
            assert reply["ok"] and reply["execution"]["status"] == "completed"
            assert reply["execution"]["temporalRunId"] == original.first_execution_run_id
            assert (await neighbor.describe()).status.name == "RUNNING"
            result = await execute(other, action)
            assert not result.errors
            assert result.data["controlWorkflowExecution"]["ok"]
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(neighbor.result(), 10)
            assert (await neighbor.describe()).status.name == (
                "CANCELED" if action == "cancel" else "TERMINATED"
            )
        finally:
            if (await neighbor.describe()).status.name == "RUNNING":
                await neighbor.terminate(reason="disposable verification cleanup")
