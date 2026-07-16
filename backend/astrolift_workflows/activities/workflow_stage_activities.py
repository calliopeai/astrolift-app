"""
Temporal activities for the WorkflowDefinition stage executor.

These back ``WorkflowDefinitionRunWorkflow`` — the durable executor that
walks an agent ``workflows.WorkflowDefinition``'s ordered ``WorkflowStage``
rows and runs each one (agent dispatch, human gate, checkpoint,
aggregation, fan-out).

Design notes
------------
* **Activities are thin.** Each one is a single DB transaction (plus, for
  ``dispatch_agent_for_stage``, one spawner call). All orchestration,
  retry/skip/escalate policy, and fan-out arithmetic lives in the
  workflow body / its pure policy module — never here. Workflow code
  must stay free of Django + driver imports, so every Django-touching
  helper is a sync ``_xxx_sync`` function invoked via ``sync_to_async``.

* **Every ``save`` carries ``version`` + ``updated_at``.** The platform's
  ``BaseCoreModel`` uses optimistic concurrency; omitting them silently
  drops the bump and lets a stale concurrent write win.

* **Human gates need no new table.** A ``HUMAN_GATE`` stage is represented
  entirely by its ``WorkflowStageExecution`` row plus a Temporal signal
  (``human_gate_decision``). The signal is the durable record of the
  operator's decision; ``record_human_gate_decision`` persists the
  outcome onto the execution row's ``output`` for the operator UI and the
  audit trail. There is intentionally no ``HumanGate`` model.

* **AgentRun is the polled source of truth.** ``dispatch_agent_for_stage``
  creates the fleet-history ``astrolift_lifecycle.AgentRun`` row (linked
  from the ``WorkflowStageExecution``) and dispatches the actual work as
  an ``astrolift_agents.AgentTask`` through the ``astrolift_dispatch``
  spawner. ``poll_agent_run_status`` reconciles the AgentTask's container
  status onto the AgentRun and returns the AgentRun status — so push-mode
  callbacks that flip AgentRun directly are honoured too.
"""

from __future__ import annotations

import logging
import uuid

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.workflow_stage")


def _unique_slug(prefix: str) -> str:
    """A globally-unique slug for a stage-execution row.

    ``WorkflowStageExecution`` (via ``core.models.common.BaseCoreModel``)
    carries a ``unique=True`` slug whose ``save()`` slugifies ``name`` when
    blank — an unset slug becomes the literal ``"none"`` and the *second*
    such row in the table collides. The executor creates many execution
    rows (one per stage, per fan-out branch, per retry attempt), so each
    one needs its own slug. A short uuid suffix guarantees uniqueness
    across runs, fan-out branches, and retries.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _parent_run_pk(workflow_run_id: str) -> int:
    """Resolve the WorkflowRun pk from a (possibly composite) run id.

    Fan-out children run under ``<parent_run_pk>:fanout:<order>:<idx>`` (see
    _run_fan_out); their activities still operate on the PARENT WorkflowRun.
    A plain run id ("38") has no colon and round-trips unchanged. Without this
    every fan-out child raised ``ValueError: invalid literal for int()`` and
    the aggregation saw 0 children (#1017).
    """
    return int(str(workflow_run_id).split(":", 1)[0])


# ---------------------------------------------------------------------------
# Sync helpers (Django-touching — called via sync_to_async)
# ---------------------------------------------------------------------------


def _get_workflow_stages_sync(workflow_definition_slug: str) -> list[dict]:
    """Return the definition's stages as ordered plain dicts.

    The workflow body keys off these dicts, never the ORM objects, so the
    sandbox stays free of Django. ``stage_id`` is the DB pk used by the
    per-stage execution activities.
    """
    from workflows.models import WorkflowDefinition

    definition = (
        WorkflowDefinition.objects.filter(
            slug=workflow_definition_slug,
            is_enabled=True,
            deleted_at__isnull=True,
        )
        .prefetch_related("stages")
        .first()
    )
    if definition is None:
        raise RuntimeError(f"WorkflowDefinition {workflow_definition_slug!r} not found, disabled, or deleted")

    stages: list[dict] = []
    for stage in definition.stages.filter(deleted_at__isnull=True).order_by("order"):
        stages.append(
            {
                "stage_id": str(stage.pk),
                "order": stage.order,
                "kind": stage.kind,
                "on_failure": stage.on_failure,
                "timeout_seconds": int(stage.timeout_seconds),
                "fan_out_count": stage.fan_out_count,
                "skill_refs": list(stage.skill_refs or []),
                "agent_definition_id": stage.agent_definition_id,
                "has_agent_definition": stage.agent_definition_id is not None,
            }
        )
    return {
        "pattern_kind": definition.pattern_kind,
        "stages": stages,
    }


def _create_stage_execution_sync(
    workflow_run_id: str,
    stage_id: str,
    attempt_number: int,
) -> str:
    """Create a RUNNING ``WorkflowStageExecution`` and point the run's
    ``current_stage_execution`` at it. Returns the execution pk as str."""
    from django.utils import timezone

    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStage, WorkflowStageExecution

    run = WorkflowRun.objects.get(pk=_parent_run_pk(workflow_run_id))
    stage = WorkflowStage.objects.get(pk=int(stage_id))

    attempt = max(1, int(attempt_number))
    execution = WorkflowStageExecution.objects.create(
        slug=_unique_slug(f"wfse-{run.pk}-{stage.pk}-a{attempt}"),
        workflow_run=run,
        stage=stage,
        status=WorkflowStageExecution.Status.RUNNING,
        attempt_number=attempt,
        started_at=timezone.now(),
    )

    run.current_stage_execution = execution
    run.save(update_fields=["current_stage_execution", "updated_at", "version"])
    return str(execution.pk)


def _update_stage_execution_sync(
    execution_id: str,
    status: str,
    output: dict | None,
    error: str | None,
) -> None:
    """Move an execution to ``status``, recording output / error.

    Refuses to overwrite a row that has already reached a terminal status
    (the executions table is append-only after termination) so a late
    retry or duplicate activity delivery can't resurrect a finished stage.
    """
    from django.utils import timezone

    from workflows.models import WorkflowStageExecution

    valid = {c[0] for c in WorkflowStageExecution.Status.choices}
    if status not in valid:
        raise ValueError(f"invalid stage execution status {status!r}")

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    if execution.is_terminal:
        log.info(
            "update_stage_execution: %s already terminal (%s); ignoring %s",
            execution_id,
            execution.status,
            status,
        )
        return

    execution.status = status
    if output is not None:
        execution.output = output
    if error:
        execution.error_message = error
        execution.failure = {"message": error}
    if status in WorkflowStageExecution.TERMINAL_STATUSES:
        execution.ended_at = timezone.now()

    execution.save(
        update_fields=[
            "status",
            "output",
            "error_message",
            "failure",
            "ended_at",
            "updated_at",
            "version",
        ]
    )


def _resolve_dispatcher_sync(organization_id: int | None):
    """Pick an ACTIVE DispatcherInstance for the org, preferring a local
    backend in dev. Returns the instance or None when none is registered."""
    from astrolift_agents.models import DispatcherInstance

    qs = DispatcherInstance.objects.filter(
        status=DispatcherInstance.Status.ACTIVE,
        deleted_at__isnull=True,
    )
    if organization_id is not None:
        qs = qs.filter(organization_id=organization_id)
    # Deterministic pick: oldest first so re-dispatch is stable.
    return qs.order_by("created_at").first()


def _dispatch_agent_for_stage_sync(
    stage_id: str,
    execution_id: str,
    trigger_payload: dict,
) -> str:
    """Create the AgentRun history row, enqueue + spawn an AgentTask, and
    link the run onto the stage execution. Returns the AgentRun pk.

    The AgentRun is the record ``poll_agent_run_status`` reconciles
    against; the AgentTask is the actual dispatch unit handed to the
    spawner. The two are bridged by stamping the spawner's ``external_id``
    onto the AgentTask and keeping AgentRun status in lockstep.
    """
    from django.utils import timezone

    from astrolift_agents.models import AgentTask
    from astrolift_dispatch.spawners.registry import get_spawner
    from astrolift_lifecycle.models import AgentRun
    from workflows.models import WorkflowStage, WorkflowStageExecution

    stage = WorkflowStage.objects.select_related("agent_definition", "definition").get(pk=int(stage_id))
    execution = WorkflowStageExecution.objects.select_related("workflow_run").get(pk=int(execution_id))
    if stage.agent_definition is None:
        raise RuntimeError(
            f"stage {stage_id} is kind={stage.kind} with no agent_definition — " "cannot dispatch an agent"
        )

    run = execution.workflow_run
    organization_id = run.organization_id

    agent_run = AgentRun.objects.create(
        workload=stage.agent_definition,
        trigger_kind=AgentRun.TriggerKind.EVENT,
        triggered_by_user_id=run.trigger_actor_user_id,
        status=AgentRun.Status.PENDING,
        input={
            "stage_id": str(stage.pk),
            "stage_order": stage.order,
            "skill_refs": list(stage.skill_refs or []),
            "trigger_payload": trigger_payload,
        },
        started_at=timezone.now(),
    )

    # Link the run onto the execution immediately so an operator polling
    # the stage sees the dispatch even if the spawn below is slow/fails.
    execution.agent_run = agent_run
    execution.save(update_fields=["agent_run", "updated_at", "version"])

    task = AgentTask.objects.create(
        organization_id=organization_id,
        agent_definition=stage.agent_definition,
        status=AgentTask.Status.DRAFT,
        timeout_seconds=int(stage.timeout_seconds),
    )
    task.transition_to(AgentTask.Status.QUEUED)

    dispatcher = _resolve_dispatcher_sync(organization_id)
    if dispatcher is None:
        # No dispatcher registered — leave the AgentRun PENDING. The
        # workflow's timeout governs how long it waits; an operator (or a
        # push-mode callback) can still advance the run. We do NOT fail
        # the dispatch here because registration may be in flight.
        log.warning(
            "dispatch_agent_for_stage: no ACTIVE dispatcher for org=%s; " "AgentRun %s left PENDING",
            organization_id,
            agent_run.pk,
        )
        return str(agent_run.pk)

    task.transition_to(AgentTask.Status.PROVISIONING)
    task.dispatcher = dispatcher

    # Spawn into the per-org agent namespace (the same one execute_agent_stage
    # uses) and freeze it on the task. Previously this path took the spawner's
    # "default" namespace while the log resolver read the per-org namespace, so
    # agentTaskLogs always came back empty for stage-dispatched agents (#891).
    from astrolift_workflows.activities.agent_stage import _agent_namespace

    namespace = _agent_namespace(run.organization.slug)
    spawner = get_spawner(dispatcher.backend, cluster=dispatcher.tenant_cluster, namespace=namespace)
    result = spawner.spawn(task)
    task.external_id = result.external_id
    task.namespace = namespace
    task.pod_name = result.external_id
    task.save(
        update_fields=[
            "external_id",
            "dispatcher",
            "namespace",
            "pod_name",
            "updated_at",
            "version",
        ]
    )

    if not result.ok:
        task.transition_to(AgentTask.Status.FAILED)
        agent_run.status = AgentRun.Status.FAILED
        agent_run.ended_at = timezone.now()
        agent_run.output = {"spawn_error": result.error}
        agent_run.save(update_fields=["status", "ended_at", "output", "updated_at", "version"])
        raise RuntimeError(
            f"spawn failed for stage {stage_id} via dispatcher " f"{dispatcher.slug!r}: {result.error}"
        )

    task.transition_to(AgentTask.Status.RUNNING)
    agent_run.status = AgentRun.Status.RUNNING
    agent_run.k8s_pod_name = result.external_id
    agent_run.save(update_fields=["status", "k8s_pod_name", "updated_at", "version"])
    return str(agent_run.pk)


def _poll_agent_run_status_sync(agent_run_id: str) -> str:
    """Reconcile the AgentRun's dispatched AgentTask onto the AgentRun and
    return the AgentRun status string.

    Already-terminal AgentRuns short-circuit. Otherwise the linked
    AgentTask's container status (via the spawner) is mapped onto both the
    task and the run. A push-mode callback may have already moved the run
    to a terminal state — that wins and is returned as-is.
    """
    from django.utils import timezone

    from astrolift_agents.models import AgentTask
    from astrolift_dispatch.spawners.registry import get_spawner
    from astrolift_lifecycle.models import AgentRun

    agent_run = AgentRun.objects.get(pk=int(agent_run_id))
    terminal = {
        AgentRun.Status.SUCCEEDED,
        AgentRun.Status.FAILED,
        AgentRun.Status.CANCELLED,
    }
    if agent_run.status in terminal:
        return agent_run.status

    # Find the dispatched task. Tasks are linked by the same workload +
    # dispatch window; the stage's execution carries no direct FK to the
    # task, so we locate the most recent non-terminal task for the run's
    # workload that still has a live external_id.
    task = (
        AgentTask.objects.filter(
            agent_definition=agent_run.workload,
            external_id=agent_run.k8s_pod_name,
            deleted_at__isnull=True,
        )
        .exclude(external_id="")
        .order_by("-created_at")
        .first()
    )
    if task is None or not task.external_id or task.dispatcher_id is None:
        # Nothing to poll (no dispatcher / push-mode only). Leave as-is.
        return agent_run.status

    dispatcher = task.dispatcher
    spawner = get_spawner(dispatcher.backend, cluster=dispatcher.tenant_cluster)
    try:
        status = spawner.status(task.external_id)
    except Exception as exc:  # noqa: BLE001 — treat poll failures as transient
        log.warning("poll_agent_run_status: status() failed for %s: %s", agent_run_id, exc)
        return agent_run.status

    if status.succeeded:
        if task.status == AgentTask.Status.RUNNING:
            task.transition_to(AgentTask.Status.COMPLETED)
        agent_run.status = AgentRun.Status.SUCCEEDED
        agent_run.ended_at = timezone.now()
        agent_run.output = {"exit_code": status.exit_code or 0}
        agent_run.save(update_fields=["status", "ended_at", "output", "updated_at", "version"])
    elif status.failed:
        if task.status == AgentTask.Status.RUNNING:
            task.transition_to(AgentTask.Status.FAILED)
        agent_run.status = AgentRun.Status.FAILED
        agent_run.ended_at = timezone.now()
        agent_run.output = {
            "exit_code": status.exit_code,
            "error": status.error_message,
        }
        agent_run.save(update_fields=["status", "ended_at", "output", "updated_at", "version"])
    # else: still running — no change.
    return agent_run.status


def _record_human_gate_decision_sync(
    execution_id: str,
    decision: str,
    decided_by_user_id: int | None,
    note: str,
) -> None:
    """Persist a human-gate outcome onto the gate's execution row.

    There is no separate gate table; the ``WorkflowStageExecution`` row is
    the durable record. ``approved`` completes the stage, ``rejected``
    fails it. The decision metadata is stored on ``output`` so the
    operator UI / audit log can show who decided and why.
    """
    from django.utils import timezone

    from workflows.models import WorkflowStageExecution

    if decision not in ("approved", "rejected"):
        raise ValueError(f"invalid human-gate decision {decision!r}")

    execution = WorkflowStageExecution.objects.get(pk=int(execution_id))
    if execution.is_terminal:
        log.info(
            "record_human_gate_decision: %s already terminal (%s); ignoring",
            execution_id,
            execution.status,
        )
        return

    execution.output = {
        "human_gate": {
            "decision": decision,
            "decided_by_user_id": decided_by_user_id,
            "note": note or "",
        }
    }
    if decision == "approved":
        execution.status = WorkflowStageExecution.Status.COMPLETED
    else:
        execution.status = WorkflowStageExecution.Status.FAILED
        execution.error_message = "human gate rejected"
        execution.failure = {"message": "human gate rejected", "note": note or ""}
    execution.ended_at = timezone.now()
    execution.save(
        update_fields=[
            "status",
            "output",
            "error_message",
            "failure",
            "ended_at",
            "updated_at",
            "version",
        ]
    )


def _snapshot_checkpoint_sync(
    workflow_run_id: str,
    stage_id: str,
    previous_output: dict | None,
) -> str:
    """Create an already-COMPLETED checkpoint execution that snapshots the
    prior stage's output. Returns the execution pk."""
    from django.utils import timezone

    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStage, WorkflowStageExecution

    run = WorkflowRun.objects.get(pk=_parent_run_pk(workflow_run_id))
    stage = WorkflowStage.objects.get(pk=int(stage_id))
    now = timezone.now()
    execution = WorkflowStageExecution.objects.create(
        slug=_unique_slug(f"wfse-{run.pk}-{stage.pk}-checkpoint"),
        workflow_run=run,
        stage=stage,
        status=WorkflowStageExecution.Status.COMPLETED,
        attempt_number=1,
        started_at=now,
        ended_at=now,
        output={"checkpoint": previous_output or {}},
    )
    run.current_stage_execution = execution
    run.save(update_fields=["current_stage_execution", "updated_at", "version"])
    return str(execution.pk)


def _aggregate_fan_out_sync(
    workflow_run_id: str,
    stage_id: str,
    source_execution_ids: list[str],
) -> dict:
    """Merge the outputs of the fan-out source executions and persist an
    AGGREGATION execution linking them via ``fan_out_sources``.

    Returns ``{"aggregated": [...], "ok_count": int, "total": int}`` so the
    workflow can decide whether the aggregation succeeded.
    """
    from django.utils import timezone

    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStage, WorkflowStageExecution

    run = WorkflowRun.objects.get(pk=_parent_run_pk(workflow_run_id))
    stage = WorkflowStage.objects.get(pk=int(stage_id))
    sources = list(WorkflowStageExecution.objects.filter(pk__in=[int(s) for s in source_execution_ids]))

    merged: list[dict] = []
    ok_count = 0
    for src in sources:
        completed = src.status == WorkflowStageExecution.Status.COMPLETED
        if completed:
            ok_count += 1
        merged.append(
            {
                "execution_id": str(src.pk),
                "status": src.status,
                "output": src.output,
            }
        )

    aggregated = {
        "aggregated": merged,
        "ok_count": ok_count,
        "total": len(sources),
    }

    now = timezone.now()
    execution = WorkflowStageExecution.objects.create(
        slug=_unique_slug(f"wfse-{run.pk}-{stage.pk}-agg"),
        workflow_run=run,
        stage=stage,
        status=WorkflowStageExecution.Status.COMPLETED,
        attempt_number=1,
        started_at=now,
        ended_at=now,
        output=aggregated,
    )
    if sources:
        execution.fan_out_sources.set(sources)
    run.current_stage_execution = execution
    run.save(update_fields=["current_stage_execution", "updated_at", "version"])
    return aggregated


def _mark_workflow_run_sync(
    workflow_run_id: str,
    status: str,
    result: dict | None,
    failure: dict | None,
) -> None:
    """Move the WorkflowRun mirror row to a terminal status and clear the
    ``current_stage_execution`` pointer."""
    from django.utils import timezone

    from astrolift_operations.models import WorkflowRun

    valid = {c[0] for c in WorkflowRun.Status.choices}
    if status not in valid:
        raise ValueError(f"invalid workflow run status {status!r}")

    run = WorkflowRun.objects.get(pk=_parent_run_pk(workflow_run_id))
    run.status = status
    if result is not None:
        run.result = result
    if failure is not None:
        run.failure = failure
    if status != WorkflowRun.Status.RUNNING:
        run.ended_at = timezone.now()
        run.current_stage_execution = None
    run.save(
        update_fields=[
            "status",
            "result",
            "failure",
            "ended_at",
            "current_stage_execution",
            "updated_at",
            "version",
        ]
    )


# ---------------------------------------------------------------------------
# Temporal activity definitions
# ---------------------------------------------------------------------------


@activity.defn(name="astrolift.workflow_stage.get_workflow_stages")
async def get_workflow_stages(workflow_definition_slug: str) -> dict:
    """Return ``{pattern_kind, stages: [ordered stage dicts]}``."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_get_workflow_stages_sync)(workflow_definition_slug)


@activity.defn(name="astrolift.workflow_stage.create_stage_execution")
async def create_stage_execution(
    workflow_run_id: str,
    stage_id: str,
    attempt_number: int = 1,
) -> str:
    """Create a RUNNING WorkflowStageExecution; return its pk as str."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_create_stage_execution_sync)(workflow_run_id, stage_id, attempt_number)


@activity.defn(name="astrolift.workflow_stage.update_stage_execution")
async def update_stage_execution(
    execution_id: str,
    status: str,
    output: dict | None = None,
    error: str | None = None,
) -> None:
    """Transition an execution row to ``status`` with optional output/error."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_update_stage_execution_sync)(execution_id, status, output, error)


@activity.defn(name="astrolift.workflow_stage.dispatch_agent_for_stage")
async def dispatch_agent_for_stage(
    stage_id: str,
    execution_id: str,
    trigger_payload: dict,
) -> str:
    """Create an AgentRun + dispatch an AgentTask; return the AgentRun pk."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_dispatch_agent_for_stage_sync)(stage_id, execution_id, trigger_payload)


@activity.defn(name="astrolift.workflow_stage.poll_agent_run_status")
async def poll_agent_run_status(agent_run_id: str) -> str:
    """Return the current AgentRun status, reconciling the dispatched task."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_poll_agent_run_status_sync)(agent_run_id)


@activity.defn(name="astrolift.workflow_stage.record_human_gate_decision")
async def record_human_gate_decision(
    execution_id: str,
    decision: str,
    decided_by_user_id: int | None = None,
    note: str = "",
) -> None:
    """Persist a human-gate ``approved``/``rejected`` outcome on the row."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_record_human_gate_decision_sync)(execution_id, decision, decided_by_user_id, note)


@activity.defn(name="astrolift.workflow_stage.snapshot_checkpoint")
async def snapshot_checkpoint(
    workflow_run_id: str,
    stage_id: str,
    previous_output: dict | None = None,
) -> str:
    """Create a COMPLETED checkpoint execution snapshotting prior output."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_snapshot_checkpoint_sync)(workflow_run_id, stage_id, previous_output)


@activity.defn(name="astrolift.workflow_stage.aggregate_fan_out")
async def aggregate_fan_out(
    workflow_run_id: str,
    stage_id: str,
    source_execution_ids: list[str],
) -> dict:
    """Merge fan-out source outputs into one AGGREGATION execution."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_aggregate_fan_out_sync)(workflow_run_id, stage_id, source_execution_ids)


@activity.defn(name="astrolift.workflow_stage.mark_workflow_run")
async def mark_workflow_run(
    workflow_run_id: str,
    status: str,
    result: dict | None = None,
    failure: dict | None = None,
) -> None:
    """Move the WorkflowRun mirror to a terminal status."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_workflow_run_sync)(workflow_run_id, status, result, failure)
