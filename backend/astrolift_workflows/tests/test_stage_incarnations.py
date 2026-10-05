"""Real database identity ownership and lost activity response recovery."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.db import IntegrityError, close_old_connections, connection, transaction
from temporalio import activity

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities import workflow_stage_activities as activities
from astrolift_workflows.schema.stage_temporal_types import stage_temporal_execution
from astrolift_workflows.schema.workflow_config_types import pending_gate_to_type
from astrolift_workflows.tests.test_serial_collections_temporal_2156 import (
    REGISTERED,
    create_plan,
    replay_family,
    start,
)
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from core.testing.temporal import temporal_worker
from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution
from workflows.stage_incarnations import StageIncarnation

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world():
    org = Organization.objects.create(name="Stage identity", slug="stage-identity")
    definition = WorkflowDefinition.objects.create(organization=org, name="Identity", slug="stage-identity")
    stage = WorkflowStage.objects.create(definition=definition, order=0, kind="checkpoint")
    run = WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="stage-identity",
        status="running",
        run_id=str(uuid4()),
    )
    identity = StageIncarnation("test-namespace", run.workflow_id, run.run_id, "3")
    return SimpleNamespace(run=run, stage=stage, identity=identity)


def open_stage(world, *, identity=True, context=None, attempt=1):
    pk = activities._create_stage_execution_sync(
        str(world.run.pk),
        str(world.stage.pk),
        attempt,
        context,
        temporal_identity=world.identity if identity else None,
    )
    return WorkflowStageExecution.objects.get(pk=pk)


@pytest.mark.parametrize("context", [None, {}])
def test_retry_returns_same_execution_even_after_run_closes(world, context):
    row = open_stage(world, context=context)
    world.run.refresh_from_db()
    world.run.status = "completed"
    world.run.save()
    before = row.version
    repeated = open_stage(world, context=context)
    assert repeated.pk == row.pk and repeated.version == before
    assert WorkflowStageExecution.objects.count() == 1
    assert row.temporal_activity_key == world.identity.fields()["temporal_activity_key"]
    assert row.history.latest().temporal_run_id == world.identity.run_id


def test_activity_cannot_rebind_an_existing_logical_stage_to_a_new_run(world):
    original = open_stage(world, context={})
    world.identity = replace(world.identity, run_id=str(uuid4()))
    with pytest.raises(RuntimeError, match="different Temporal activity"):
        open_stage(world, context={})
    original.refresh_from_db()
    assert original.temporal_run_id != world.identity.run_id
    assert WorkflowStageExecution.objects.count() == 1


@pytest.mark.parametrize("changed", ["run", "stage", "attempt"])
def test_activity_identity_cannot_be_reused_for_different_arguments(world, changed):
    open_stage(world)
    attempt = 1
    if changed == "stage":
        world.stage = WorkflowStage.objects.create(
            definition=world.stage.definition, order=1, kind="checkpoint"
        )
    elif changed == "run":
        world.run = WorkflowRun.objects.create(
            organization=world.run.organization,
            workflow_definition=world.run.workflow_definition,
            workflow_kind="WorkflowDefinitionRunWorkflow",
            workflow_id="another-run",
            status="running",
        )
    else:
        attempt = 2
    with pytest.raises(RuntimeError, match="conflicts"):
        open_stage(world, attempt=attempt)
    assert WorkflowStageExecution.objects.count() == 1


def test_legacy_row_is_never_backfilled_from_a_retry_or_parent_identity(world):
    original = open_stage(world, identity=False, context={})
    repeated = open_stage(world, context={})
    assert original.pk == repeated.pk and repeated.temporal_activity_key == ""
    assert stage_temporal_execution(repeated) is None
    gate = pending_gate_to_type(repeated)
    assert gate.execution_guid == str(original.guid)
    assert gate.stage_guid == str(world.stage.guid)
    assert gate.temporal_execution is None


def test_database_enforces_one_row_per_source_activity(world):
    row = open_stage(world)
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkflowStageExecution.objects.create(
            workflow_run=world.run, stage=world.stage, slug="duplicate-source", **world.identity.fields()
        )
    assert WorkflowStageExecution.objects.count() == 1
    assert stage_temporal_execution(row).run_id == world.identity.run_id


def test_concurrent_duplicate_activity_deliveries_recover_one_stage(world):
    barrier = Barrier(2)

    def deliver():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            return open_stage(world).pk
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(deliver) for _ in range(2)]
        identities = [future.result(timeout=10) for future in pending]
    assert identities[0] == identities[1]
    assert WorkflowStageExecution.objects.count() == 1


def test_incomplete_or_corrupted_identity_is_not_advertised(world):
    row = open_stage(world)
    row.temporal_run_id = str(uuid4())
    assert stage_temporal_execution(row) is None
    row.temporal_run_id = ""
    assert stage_temporal_execution(row) is None


@pytest.mark.parametrize(
    "field,value",
    [("namespace", ""), ("workflow_id", "x" * 513), ("run_id", "not-a-run"), ("activity_id", None)],
)
def test_invalid_worker_identity_is_rejected(world, field, value):
    with pytest.raises((TypeError, ValueError)):
        replace(world.identity, **{field: value})


@pytest.mark.parametrize("kind", ["format_record", "workflow"])
async def test_real_root_collection_and_nested_activity_retry_keeps_exact_incarnation(temporal_env, kind):
    plan = await sync_to_async(create_plan)(kind=kind)
    deliveries = {}

    @activity.defn(name="astrolift.workflow_stage.create_stage_execution")
    async def lose_first_response(
        workflow_run_id: str, stage_id: str, attempt_number: int = 1, context: dict | None = None
    ) -> str:
        pk = await activities.create_stage_execution(workflow_run_id, stage_id, attempt_number, context)
        source = activity.info()
        key = (source.workflow_run_id, source.activity_id)
        deliveries.setdefault(key, []).append(pk)
        if len(deliveries[key]) == 1:
            raise RuntimeError("Disposable lost stage-creation response")
        return pk

    registered = [lose_first_response if fn is activities.create_stage_execution else fn for fn in REGISTERED]
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=registered
    ):
        handle = await start(temporal_env, plan, {"items": [{"text": "one"}, {"text": "two"}]})
        result = await asyncio.wait_for(handle.result(), 40)
        history = await handle.fetch_history()
    assert result.ok
    rows = await sync_to_async(list)(WorkflowStageExecution.objects.order_by("pk"))
    assert len(rows) == (5 if kind == "workflow" else 3) == len(deliveries)
    assert all(len(pks) == 2 and len(set(pks)) == 1 for pks in deliveries.values())
    root_run_id = (await handle.describe()).run_id
    for row in rows:
        assert row.temporal_namespace == temporal_env.client.namespace
        assert stage_temporal_execution(row) is not None
        owner = temporal_env.client.get_workflow_handle(row.temporal_workflow_id, run_id=row.temporal_run_id)
        actual = await owner.describe()
        assert actual.run_id == row.temporal_run_id
        if row.collection_parent_execution_id:
            assert row.temporal_workflow_id == row.collection_workflow_id != plan[3]
            assert row.temporal_run_id != root_run_id
        elif row.workflow_run_id == plan[2]:
            assert row.temporal_workflow_id == plan[3] and row.temporal_run_id == root_run_id
        else:
            nested = await sync_to_async(WorkflowRun.objects.get)(pk=row.workflow_run_id)
            assert row.temporal_workflow_id == nested.workflow_id and row.temporal_run_id == nested.run_id
    await replay_family(temporal_env, history)
