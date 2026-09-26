"""Every fire of a configured Workflow's schedule gets its own run (#2053).

`workflows/schedule_sync.py` built one `WorkflowRun` when the Workflow was
saved and baked its `WorkflowDefinitionRunInput` into the schedule action.
Every fire ran that one row. The first fire closed it, and every later fire
failed at its first stage with "Cannot open a stage on a closed workflow".
No fire wrote a run record of its own, so `runCount` stayed 0.

The schedule now names the Workflow, not a run. Each fire starts
`ConfiguredWorkflowScheduleWorkflow`, which creates the run and runs the stage
executor as its child, the split `PipelineScheduleWorkflow` makes (#1614).
"""

from __future__ import annotations

import asyncio
import uuid
from io import StringIO

import pytest
from asgiref.sync import sync_to_async
from django.core.management import CommandError, call_command
from django.utils import timezone

from astrolift_operations.models import WorkflowRun
from astrolift_workflows.inputs import Actor
from workflows.models import Workflow, WorkflowDefinition, WorkflowStage, WorkflowStageExecution

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Sched Org", slug=f"sched-{uuid.uuid4().hex[:8]}")


def _scheduled_workflow(org, *, slug="nightly-sweep"):
    definition = WorkflowDefinition.objects.create(
        name="Sweep",
        slug=f"sweep-{uuid.uuid4().hex[:8]}",
        organization=org,
        model_label="",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        states=[],
        transitions=[],
        is_enabled=True,
    )
    WorkflowStage.objects.create(
        definition=definition,
        slug=f"{definition.slug}-0",
        order=0,
        kind=WorkflowStage.StageKind.CHECKPOINT,
        role="snapshot",
    )
    return Workflow.objects.create(
        organization=org,
        definition=definition,
        name="Nightly sweep",
        slug=slug,
        trigger_kind=Workflow.TriggerKind.SCHEDULE,
        schedule_cron="0 13 * * *",
        inputs={"window": "24h"},
        stage_bindings={"0": {"params": {"output_key": "sweep"}}},
    )


@pytest.fixture
def scheduled(org):
    return _scheduled_workflow(org)


def _action(wf):
    return {"workflow_guid": str(wf.guid), "organization_id": wf.organization_id}


class _FakeScheduleHandle:
    def __init__(self, calls, schedule_id):
        self._calls = calls
        self._id = schedule_id

    async def delete(self):
        self._calls.append(("delete", self._id))


class _FakeTemporalClient:
    """Records schedule writes; `fail_create` names schedule ids to refuse."""

    def __init__(self, fail_create=()):
        self.calls = []
        self.schedules = {}
        self.fail_create = set(fail_create)

    def get_schedule_handle(self, schedule_id):
        return _FakeScheduleHandle(self.calls, schedule_id)

    async def create_schedule(self, schedule_id, schedule):
        if schedule_id in self.fail_create:
            raise RuntimeError("temporal refused the schedule")
        self.calls.append(("create", schedule_id))
        self.schedules[schedule_id] = schedule


@pytest.fixture
def temporal_client(monkeypatch):
    client = _FakeTemporalClient()

    async def _client():
        return client

    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: True)
    monkeypatch.setattr("astrolift_workflows.client._get_client_async", _client)
    return client


# ---- registration ----------------------------------------------------------


def test_the_wrapper_and_its_activities_are_registered():
    """A schedule naming an unregistered workflow fails every fire while the
    schedule itself looks healthy."""
    from astrolift_workflows.activities.configured_workflow_schedule_fire import (
        create_scheduled_workflow_run,
        record_scheduled_workflow_start,
    )
    from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS
    from astrolift_workflows.workflows import ConfiguredWorkflowScheduleWorkflow

    assert ConfiguredWorkflowScheduleWorkflow in WORKFLOWS
    assert {create_scheduled_workflow_run, record_scheduled_workflow_start} <= set(ACTIVITIES)


# ---- the schedule writer ---------------------------------------------------


def test_the_schedule_action_names_the_workflow_not_a_run(scheduled, temporal_client):
    """The defect itself: the action carried a run input, so it named one
    WorkflowRun that every fire reused. Saving a schedule now creates no run
    at all; a fire does."""
    from workflows.schedule_sync import sync_workflow_schedule

    runs_before = WorkflowRun.objects.count()

    sync_workflow_schedule(scheduled)

    schedule_id = f"workflow-{scheduled.guid}"
    action = temporal_client.schedules[schedule_id].action
    assert action.workflow == "ConfiguredWorkflowScheduleWorkflow"
    assert action.args == [_action(scheduled)]
    assert action.id == f"{schedule_id}-run"
    assert WorkflowRun.objects.count() == runs_before


# ---- one fire --------------------------------------------------------------


def test_two_fires_create_two_distinct_runs(scheduled):
    from astrolift_workflows.activities.configured_workflow_schedule_fire import _create_sync
    from astrolift_workflows.schema.workflow_config_types import workflow_to_type

    first = _create_sync(_action(scheduled))
    second = _create_sync(_action(scheduled))

    assert first.run_input.workflow_run_id != second.run_input.workflow_run_id
    for fire in (first, second):
        run = WorkflowRun.objects.get(pk=int(fire.run_input.workflow_run_id))
        assert (run.status, run.ended_at) == ("running", None)
        assert run.workflow_id == fire.workflow_id == f"WorkflowDefinitionRunWorkflow-{run.pk}"
        assert run.organization_id == scheduled.organization_id
        assert run.workflow_definition_id == scheduled.definition_id
        assert fire.run_input.workflow_definition_id == str(scheduled.definition_id)
        assert fire.run_input.trigger_payload == scheduled.inputs
        assert fire.run_input.stage_bindings == scheduled.stage_bindings
        assert fire.run_input.actor == Actor(kind="system", user_id=None, display="scheduled")
    # runCount counts the configured-run records, which no fire used to write.
    assert workflow_to_type(scheduled).run_count == 2
    assert set(scheduled.runs.values_list("temporal_workflow_id", flat=True)) == {
        first.workflow_id,
        second.workflow_id,
    }


def test_a_later_fire_opens_its_first_stage_after_an_earlier_fire_closed(scheduled):
    """The failure as it surfaced live: once the first fire's run closed, the
    next fire's first stage could not open."""
    from astrolift_workflows.activities.configured_workflow_schedule_fire import _create_sync
    from astrolift_workflows.activities.workflow_stage_activities import (
        _create_stage_execution_sync,
        _mark_workflow_run_sync,
    )

    stage_id = str(scheduled.definition.stages.get().pk)
    first = _create_sync(_action(scheduled))
    _mark_workflow_run_sync(first.run_input.workflow_run_id, "completed", {}, None)

    second = _create_sync(_action(scheduled))
    execution_id = _create_stage_execution_sync(second.run_input.workflow_run_id, stage_id, 1)

    assert WorkflowStageExecution.objects.get(pk=int(execution_id)).workflow_run_id == int(
        second.run_input.workflow_run_id
    )
    with pytest.raises(RuntimeError, match="closed workflow"):
        _create_stage_execution_sync(first.run_input.workflow_run_id, stage_id, 1)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"deleted_at": timezone.now()}, "deleted"),
        ({"is_enabled": False}, "disabled"),
        ({"trigger_kind": Workflow.TriggerKind.MANUAL}, "no longer schedule-triggered"),
        ({"schedule_cron": None}, "no longer schedule-triggered"),
    ],
)
def test_a_fire_after_the_workflow_left_its_schedule_is_skipped_not_failed(scheduled, change, reason):
    """Temporal keeps firing until something deletes the schedule, so a fire
    that outlives its Workflow's schedule by a tick is ordinary. A red run for
    it would train operators to ignore red runs."""
    from astrolift_workflows.activities.configured_workflow_schedule_fire import _create_sync

    Workflow.objects.filter(pk=scheduled.pk).update(**change)
    runs_before = WorkflowRun.objects.count()

    fire = _create_sync(_action(scheduled))

    assert reason in fire.skipped
    assert fire.run_input is None
    assert WorkflowRun.objects.count() == runs_before
    assert not scheduled.runs.exists()


def test_an_unknown_or_foreign_workflow_is_skipped(scheduled):
    from astrolift_identity.models import Organization
    from astrolift_workflows.activities.configured_workflow_schedule_fire import _create_sync

    other = Organization.objects.create(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")

    unknown = _create_sync({"workflow_guid": str(uuid.uuid4()), "organization_id": scheduled.organization_id})
    foreign = _create_sync({"workflow_guid": str(scheduled.guid), "organization_id": other.pk})

    assert unknown.skipped
    assert foreign.skipped
    assert not WorkflowRun.objects.filter(organization_id__in=[scheduled.organization_id, other.pk]).exists()


def test_recording_the_start_stamps_the_run_and_its_record(scheduled):
    """The reconciler skips a run with no Temporal run id, and the stage
    reader keys on the id pair. The manual path stamps both when the start
    returns; a fire can only do it once the child has started."""
    from astrolift_workflows.activities.configured_workflow_schedule_fire import (
        _create_sync,
        _record_start_sync,
    )

    fire = _create_sync(_action(scheduled))

    _record_start_sync(fire.run_input.workflow_run_id, "temporal-run-1")
    _record_start_sync(fire.run_input.workflow_run_id, "temporal-run-1")  # a retried activity

    assert WorkflowRun.objects.get(pk=int(fire.run_input.workflow_run_id)).run_id == "temporal-run-1"
    assert scheduled.runs.get().temporal_run_id == "temporal-run-1"
    with pytest.raises(RuntimeError, match="different Temporal run id"):
        _record_start_sync(fire.run_input.workflow_run_id, "temporal-run-2")


# ---- the fire on a real worker ---------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_two_fires_of_one_schedule_action_each_run_to_completion(scheduled, temporal_env):
    """The seam: the same action, fired twice on the sandboxed runner
    production uses, runs the real stage executor to completion twice, on two
    runs, each stamped with its own execution."""
    from temporalio.worker import Worker
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
    from temporalio.workflow import NondeterminismError

    from astrolift_workflows.activities.configured_workflow_schedule_fire import (
        create_scheduled_workflow_run,
        record_scheduled_workflow_start,
    )
    from astrolift_workflows.activities.workflow_stage_activities import (
        get_workflow_stages,
        mark_workflow_run,
        snapshot_checkpoint,
    )
    from astrolift_workflows.schema.workflow_config_types import workflow_to_type
    from astrolift_workflows.workflows import (
        ConfiguredWorkflowScheduleWorkflow,
        WorkflowDefinitionRunWorkflow,
    )

    worker = Worker(
        temporal_env.client,
        task_queue="astrolift-test",
        workflows=[ConfiguredWorkflowScheduleWorkflow, WorkflowDefinitionRunWorkflow],
        activities=[
            create_scheduled_workflow_run,
            record_scheduled_workflow_start,
            get_workflow_stages,
            snapshot_checkpoint,
            mark_workflow_run,
        ],
        workflow_runner=SandboxedWorkflowRunner(),
        workflow_failure_exception_types=[NondeterminismError],
    )
    results = []
    async with worker:
        for nominal in ("2026-09-26T13:00:00Z", "2026-09-27T13:00:00Z"):
            handle = await temporal_env.client.start_workflow(
                ConfiguredWorkflowScheduleWorkflow.run,
                _action(scheduled),
                id=f"workflow-{scheduled.guid}-run-{nominal}",
                task_queue="astrolift-test",
            )
            results.append(await asyncio.wait_for(handle.result(), timeout=120))
        executions = {
            result["workflow_id"]: (
                await temporal_env.client.get_workflow_handle(result["workflow_id"]).describe()
            )
            for result in results
        }

    assert [result["result"]["ok"] for result in results] == [True, True]
    assert len({result["workflow_run_id"] for result in results}) == 2

    @sync_to_async
    def settled():
        runs = list(WorkflowRun.objects.filter(workflow_definition=scheduled.definition).order_by("pk"))
        return (
            [(run.workflow_id, run.run_id, run.status, run.ended_at is not None) for run in runs],
            [(run.pk, WorkflowStageExecution.objects.filter(workflow_run=run).count()) for run in runs],
            sorted(
                (i.temporal_workflow_id, i.temporal_run_id, i.current_state, i.completed_at is not None)
                for i in scheduled.runs.all()
            ),
            workflow_to_type(scheduled).run_count,
        )

    runs, stage_counts, records, run_count = await settled()
    expected = sorted((wid, desc.run_id, "completed", True) for wid, desc in executions.items())
    assert sorted(runs) == expected
    assert records == expected
    assert [count for _pk, count in stage_counts] == [1, 1]
    assert run_count == 2


# ---- the operator step -----------------------------------------------------


@pytest.fixture
def population(org):
    """A live scheduled Workflow plus every way a Workflow stops being one."""
    live = _scheduled_workflow(org, slug="live")
    disabled = _scheduled_workflow(org, slug="disabled")
    deleted = _scheduled_workflow(org, slug="deleted")
    manual = _scheduled_workflow(org, slug="manual")
    Workflow.objects.filter(pk=disabled.pk).update(is_enabled=False)
    Workflow.objects.filter(pk=deleted.pk).update(deleted_at=timezone.now())
    Workflow.objects.filter(pk=manual.pk).update(trigger_kind=Workflow.TriggerKind.MANUAL, schedule_cron=None)
    return {"live": live, "others": [disabled, deleted, manual]}


def test_resync_reports_by_default_and_rewrites_on_apply(population, temporal_client):
    live = population["live"]
    live_id = f"workflow-{live.guid}"

    out = StringIO()
    call_command("resync_workflow_schedules", stdout=out)

    assert temporal_client.calls == []
    assert live_id in out.getvalue()
    assert "1 schedule(s) to rewrite" in out.getvalue()

    out = StringIO()
    call_command("resync_workflow_schedules", "--apply", stdout=out)

    assert temporal_client.schedules[live_id].action.workflow == "ConfiguredWorkflowScheduleWorkflow"
    assert temporal_client.schedules[live_id].action.args == [_action(live)]
    assert set(temporal_client.schedules) == {live_id}
    # Everything else gets the delete its own save would have issued, so a
    # schedule that outlived its Workflow stops firing the old action.
    deleted_ids = {schedule_id for kind, schedule_id in temporal_client.calls if kind == "delete"}
    assert {f"workflow-{wf.guid}" for wf in population["others"]} <= deleted_ids
    assert "1 rewritten, 0 failed" in out.getvalue()
    assert not WorkflowRun.objects.exists()


def test_resync_fails_loudly_when_a_rewrite_fails(org, temporal_client):
    broken = _scheduled_workflow(org, slug="broken")
    healthy = _scheduled_workflow(org, slug="healthy")
    temporal_client.fail_create.add(f"workflow-{broken.guid}")

    out = StringIO()
    with pytest.raises(CommandError, match="1 schedule"):
        call_command("resync_workflow_schedules", "--apply", stdout=out)

    assert f"workflow-{healthy.guid}" in temporal_client.schedules
    assert f"workflow-{broken.guid}" in out.getvalue()
    assert "temporal refused the schedule" in out.getvalue()


def test_resync_refuses_to_apply_while_temporal_is_off(population, monkeypatch):
    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: False)

    with pytest.raises(CommandError, match="Temporal is disabled"):
        call_command("resync_workflow_schedules", "--apply", stdout=StringIO())
