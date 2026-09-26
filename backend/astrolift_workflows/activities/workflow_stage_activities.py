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


def _get_workflow_stages_sync(
    workflow_definition_slug: str,
    workflow_run_id: str | None = None,
    stage_bindings: dict | None = None,
    workflow_definition_id: str | None = None,
    workflow_ancestry: list[str] | None = None,
) -> dict:
    """Return the definition's stages as ordered plain dicts.

    The workflow body keys off these dicts, never the ORM objects, so the
    sandbox stays free of Django. ``stage_id`` is the DB pk used by the
    per-stage execution activities.
    """
    from django.db.models import Q

    from astrolift_operations.models import WorkflowRun
    from astrolift_registry.models import Workload
    from workflows.composition import MAX_WORKFLOW_NESTING_DEPTH, resolve_child_definition
    from workflows.models import WorkflowDefinition, WorkflowStage

    run = None
    organization_id = None
    if workflow_run_id is not None:
        run = WorkflowRun.objects.get(pk=_parent_run_pk(workflow_run_id))
        organization_id = run.organization_id

    definitions = WorkflowDefinition.objects.filter(
        is_enabled=True,
        deleted_at__isnull=True,
    ).prefetch_related("stages")
    if workflow_definition_id:
        definitions = definitions.filter(pk=int(workflow_definition_id))
    else:
        definitions = definitions.filter(slug=workflow_definition_slug)
    if organization_id is not None:
        definitions = definitions.filter(Q(organization_id=organization_id) | Q(organization__isnull=True))
        definition = definitions.filter(organization_id=organization_id).first() or definitions.first()
    else:
        definition = definitions.first()
    if definition is None:
        raise RuntimeError(f"WorkflowDefinition {workflow_definition_slug!r} not found, disabled, or deleted")
    if run is not None and run.workflow_definition_id not in (None, definition.pk):
        raise RuntimeError("workflow run definition does not match the requested definition")

    ancestry = [str(value) for value in (workflow_ancestry or [])]
    definition_id = str(definition.pk)
    if definition_id in ancestry:
        raise RuntimeError("nested workflow cycle detected at runtime")
    if len(ancestry) > MAX_WORKFLOW_NESTING_DEPTH:
        raise RuntimeError(f"nested workflow depth exceeds {MAX_WORKFLOW_NESTING_DEPTH}")

    bindings = stage_bindings if isinstance(stage_bindings, dict) else {}
    stages: list[dict] = []
    for stage in definition.stages.filter(deleted_at__isnull=True).order_by("order"):
        binding = bindings.get(str(stage.order), bindings.get(stage.order, {}))
        if not isinstance(binding, dict):
            raise RuntimeError(f"stage {stage.order} binding must be an object")
        params = binding.get("params", {})
        if not isinstance(params, dict):
            raise RuntimeError(f"stage {stage.order} binding params must be an object")

        workload = None
        workload_guid = binding.get("agent_workload_id")
        if workload_guid:
            workload = Workload.objects.filter(
                guid=str(workload_guid),
                kind=Workload.Kind.AGENT,
                deleted_at__isnull=True,
                **(
                    {"registered_app__organization_id": organization_id}
                    if organization_id is not None
                    else {}
                ),
            ).first()
            if workload is None:
                raise RuntimeError(
                    f"stage {stage.order} binding does not resolve to a live agent in this organization"
                )
        elif stage.agent_definition_id is not None:
            workload = Workload.objects.filter(
                pk=stage.agent_definition_id,
                kind=Workload.Kind.AGENT,
                deleted_at__isnull=True,
                **(
                    {"registered_app__organization_id": organization_id}
                    if organization_id is not None
                    else {}
                ),
            ).first()
            if workload is None and stage.kind == WorkflowStage.StageKind.AGENT_DISPATCH:
                raise RuntimeError(
                    f"stage {stage.order} default agent is outside the run organization or unavailable"
                )
        elif stage.agent_ref and organization_id is not None:
            workload = Workload.objects.filter(
                registered_app__organization_id=organization_id,
                slug=stage.agent_ref,
                kind=Workload.Kind.AGENT,
                deleted_at__isnull=True,
            ).first()

        if stage.kind == WorkflowStage.StageKind.AGENT_DISPATCH and workload is None:
            raise RuntimeError(
                f"stage {stage.order} has no resolvable agent; bind agent_workload_id or register {stage.agent_ref!r}"
            )

        nested_definition = None
        if stage.kind == WorkflowStage.StageKind.WORKFLOW:
            nested_definition = resolve_child_definition(definition, stage.workflow_ref)
            if nested_definition is None:
                raise RuntimeError(
                    f"stage {stage.order} cannot resolve visible child workflow {stage.workflow_ref!r}"
                )
            if str(nested_definition.pk) in ancestry + [definition_id]:
                raise RuntimeError("nested workflow cycle detected at runtime")

        if "skill_refs" in binding:
            skill_refs = binding["skill_refs"]
            if not isinstance(skill_refs, list) or any(not isinstance(ref, str) for ref in skill_refs):
                raise RuntimeError(f"stage {stage.order} skill_refs must be a list of strings")
        else:
            skill_refs = list(stage.skill_refs or [])
        environment_spec_slug = params.get("environment_spec_slug", stage.environment_spec_slug)
        prompt = params.get("prompt", stage.prompt)
        output_key = params.get("output_key", stage.output_key) or f"stage_{stage.order}"
        if not all(isinstance(value, str) for value in (environment_spec_slug, prompt, output_key)):
            raise RuntimeError(f"stage {stage.order} runtime params must be strings")
        stages.append(
            {
                "stage_id": str(stage.pk),
                "order": stage.order,
                "kind": stage.kind,
                "on_failure": stage.on_failure,
                "timeout_seconds": int(stage.timeout_seconds),
                "fan_out_count": stage.fan_out_count,
                "skill_refs": list(skill_refs),
                "agent_definition_id": workload.pk if workload is not None else None,
                "has_agent_definition": workload is not None,
                "environment_spec_slug": environment_spec_slug,
                "prompt": prompt,
                "output_key": output_key,
                "workflow_ref": stage.workflow_ref or "",
                "nested_definition_id": (
                    str(nested_definition.pk) if nested_definition is not None else None
                ),
                "nested_definition_slug": (nested_definition.slug if nested_definition is not None else ""),
            }
        )
    output_keys = [stage["output_key"] for stage in stages]
    duplicates = sorted({key for key in output_keys if output_keys.count(key) > 1})
    if duplicates:
        raise RuntimeError("workflow stage output_key values must be unique: " + ", ".join(duplicates))
    return {
        "definition_id": str(definition.pk),
        "pattern_kind": definition.pattern_kind,
        "stages": stages,
    }


def _create_nested_workflow_run_sync(
    parent_workflow_run_id: str,
    stage_execution_id: str,
    child_definition_id: str,
) -> dict:
    """Create the child WorkflowRun mirror linked to its parent stage.

    The one-to-one stage link makes the activity idempotent across Temporal
    retries. Child visibility and project scope are re-checked at the write
    boundary rather than trusting the workflow's earlier plan payload.
    """
    from django.db import transaction
    from django.utils import timezone

    from astrolift_operations.models import WorkflowRun
    from workflows.composition import MAX_WORKFLOW_NESTING_DEPTH, resolve_child_definition
    from workflows.models import WorkflowStage, WorkflowStageExecution

    with transaction.atomic():
        parent = WorkflowRun.objects.select_for_update().get(pk=_parent_run_pk(parent_workflow_run_id))
        execution = WorkflowStageExecution.objects.select_related("stage__definition").get(
            pk=int(stage_execution_id), workflow_run=parent
        )
        if execution.stage.kind != WorkflowStage.StageKind.WORKFLOW:
            raise RuntimeError("nested child runs require a workflow stage execution")

        expected = resolve_child_definition(
            execution.stage.definition,
            execution.stage.workflow_ref,
        )
        if expected is None or expected.pk != int(child_definition_id):
            raise RuntimeError("nested child definition is unavailable or outside project scope")
        depth = int(parent.nesting_depth or 0) + 1
        if depth > MAX_WORKFLOW_NESTING_DEPTH:
            raise RuntimeError(f"nested workflow depth exceeds {MAX_WORKFLOW_NESTING_DEPTH}")

        existing = WorkflowRun.objects.filter(
            parent_stage_execution=execution,
            deleted_at__isnull=True,
        ).first()
        if existing is None:
            child = WorkflowRun.objects.create(
                workflow_kind="WorkflowDefinitionRunWorkflow",
                workflow_definition=expected,
                workflow_id="",
                run_id="",
                status=WorkflowRun.Status.RUNNING,
                started_at=timezone.now(),
                organization_id=parent.organization_id,
                parent_run=parent,
                parent_stage_execution=execution,
                nesting_depth=depth,
                trigger_actor_user_id=parent.trigger_actor_user_id,
                trigger_actor_token_kind=parent.trigger_actor_token_kind,
                trigger_actor_token_id=parent.trigger_actor_token_id,
            )
            child.workflow_id = f"WorkflowDefinitionRunWorkflow-{child.pk}"
            child.save(update_fields=["workflow_id", "updated_at", "version"])
        else:
            child = existing

    return {
        "workflow_run_id": str(child.pk),
        "workflow_run_guid": str(child.guid),
        "workflow_id": child.workflow_id,
        "definition_id": str(expected.pk),
        "definition_slug": expected.slug,
        "nesting_depth": child.nesting_depth,
    }


def _record_nested_workflow_start_sync(
    child_workflow_run_id: str,
    temporal_run_id: str,
) -> None:
    """Persist the Temporal run id assigned to a newly started child."""
    from astrolift_operations.models import WorkflowRun

    run = WorkflowRun.objects.get(
        pk=int(child_workflow_run_id),
        parent_run__isnull=False,
        parent_stage_execution__isnull=False,
    )
    if run.run_id and run.run_id != temporal_run_id:
        raise RuntimeError("nested workflow run already has a different Temporal run id")
    if run.run_id == temporal_run_id:
        return
    run.run_id = temporal_run_id
    run.save(update_fields=["run_id", "updated_at", "version"])


def _create_stage_execution_sync(
    workflow_run_id: str,
    stage_id: str,
    attempt_number: int,
) -> str:
    """Create a RUNNING ``WorkflowStageExecution`` and point the run's
    ``current_stage_execution`` at it. Returns the execution pk as str."""
    from django.db import transaction
    from django.utils import timezone

    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStage, WorkflowStageExecution

    with transaction.atomic():
        run = WorkflowRun.objects.select_for_update().get(pk=_parent_run_pk(workflow_run_id))
        if run.status != "running" or run.ended_at is not None:
            raise RuntimeError("Cannot open a stage on a closed workflow")
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

    # A human gate opening RUNNING is the moment reviewers are notified, and
    # this activity is the only durable hook at that moment. Capture a pending
    # GATE interaction so the P3 map shows the gate the instant it blocks
    # (#1217), then notify the reviewers so the gate does not sit until its
    # timeout waiting for someone to notice it in the UI (#59). Both are
    # defensive: neither may break stage creation.
    if stage.kind == WorkflowStage.StageKind.HUMAN_GATE:
        _capture_gate_interaction(execution, status="pending")
        _notify_gate_reviewers(run, stage)
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


def _dispatch_label(dispatcher, backend: str) -> str:
    """How to name the thing that spawned, for an error message."""
    return f"dispatcher {dispatcher.slug!r}" if dispatcher is not None else f"backend {backend!r}"


def _dispatch_target_sync(organization, dispatcher):
    """The ``(backend, cluster)`` a stage's agent spawns and polls through.

    A registered Dispatch Service names both. Nothing on an install
    produces one, though -- the registration endpoint exists for a
    service to register *itself*, and no installer component runs such a
    service (#1704) -- so with none registered this falls back to what
    the direct-dispatch path has always used: the ``k8s_job`` backend
    against the org's managed cluster. The two paths disagreeing is why
    ``astro agent dispatch`` worked on an install where every workflow
    stage hung.

    Raises whatever :func:`resolve_agent_cluster` raises when the org has
    no managed cluster. That is a real, reportable failure -- unlike the
    missing dispatcher, which is the normal state.
    """
    if dispatcher is not None:
        return dispatcher.backend, dispatcher.tenant_cluster
    from astrolift_agents.services.agent_cluster import resolve_agent_cluster

    return "k8s_job", resolve_agent_cluster(organization)


def _dispatch_agent_for_stage_sync(
    stage_id: str,
    execution_id: str,
    trigger_payload: dict,
    resolved_config: dict | None = None,
) -> str:
    """Create the AgentRun history row, enqueue + spawn an AgentTask, and
    link the run onto the stage execution. Returns the AgentRun pk.

    The AgentRun is the record ``poll_agent_run_status`` reconciles
    against; the AgentTask is the actual dispatch unit handed to the
    spawner. The two are bridged by stamping the spawner's ``external_id``
    onto the AgentTask and keeping AgentRun status in lockstep.
    """
    from django.db import transaction
    from django.utils import timezone

    from astrolift_agents.models import AgentEnvironmentSpec, AgentTask
    from astrolift_dispatch.spawners.registry import get_spawner
    from astrolift_lifecycle.models import AgentRun
    from astrolift_registry.models import Workload
    from workflows.models import WorkflowStage, WorkflowStageExecution

    stage = WorkflowStage.objects.select_related("agent_definition", "definition").get(pk=int(stage_id))
    execution = WorkflowStageExecution.objects.select_related("workflow_run").get(pk=int(execution_id))
    run = execution.workflow_run
    organization_id = run.organization_id
    config = resolved_config if isinstance(resolved_config, dict) else {}
    workload_id = config.get("agent_definition_id") or stage.agent_definition_id
    workload = (
        Workload.objects.filter(
            pk=workload_id,
            kind=Workload.Kind.AGENT,
            deleted_at__isnull=True,
            **({"registered_app__organization_id": organization_id} if organization_id is not None else {}),
        )
        .select_related("registered_app")
        .first()
    )
    if workload is None:
        raise RuntimeError(
            f"stage {stage_id} is kind={stage.kind} with no agent_definition resolvable — cannot dispatch"
        )
    skill_refs = list(config.get("skill_refs", stage.skill_refs or []))
    environment_spec_slug = str(config.get("environment_spec_slug", stage.environment_spec_slug) or "")
    prompt = str(config.get("prompt", stage.prompt) or "")
    output_key = str(config.get("output_key", stage.output_key) or f"stage_{stage.order}")

    environment_spec = None
    if environment_spec_slug:
        from astrolift_agents.visibility import spec_usable_by_app

        environment_spec = (
            AgentEnvironmentSpec.objects.filter(
                organization_id=organization_id,
                slug=environment_spec_slug,
                deleted_at__isnull=True,
            )
            .select_related("team", "project")
            .first()
        )
        # The stage's agent may run only with a spec its own app may use
        # (#1866): org-shared, or owned by the agent's project or team.
        if environment_spec is None or not spec_usable_by_app(environment_spec, workload.registered_app):
            raise RuntimeError(
                f"environment spec {environment_spec_slug!r} not found for stage {stage.order} organization, "
                f"or not usable by agent {workload.slug!r}"
            )

    # Temporal may retry an activity after the DB commit or even after the
    # external spawn succeeded but before the result reached the server. Lock
    # the stage execution and reuse its durable AgentRun/AgentTask so one
    # execution can never multiply agents merely because an activity retried.
    with transaction.atomic():
        execution = (
            WorkflowStageExecution.objects.select_for_update()
            .select_related("workflow_run")
            .get(pk=int(execution_id))
        )
        # A durable dispatch activity can arrive after its parent has closed.
        if (
            execution.is_terminal
            or execution.workflow_run.status != "running"
            or execution.workflow_run.ended_at is not None
        ):
            raise RuntimeError(f"stage execution {execution_id} belongs to a closed workflow or stage")
        if execution.stage_id != stage.pk:
            raise RuntimeError(f"stage execution {execution_id} does not belong to stage {stage_id}")
        agent_run = (
            AgentRun.objects.filter(pk=execution.agent_run_id).first()
            if execution.agent_run_id is not None
            else None
        )
        if agent_run is None:
            agent_run = AgentRun.objects.create(
                workload=workload,
                trigger_kind=AgentRun.TriggerKind.EVENT,
                triggered_by_user_id=run.trigger_actor_user_id,
                status=AgentRun.Status.PENDING,
                input={
                    "stage_id": str(stage.pk),
                    "stage_order": stage.order,
                    "skill_refs": skill_refs,
                    "environment_spec_slug": environment_spec_slug,
                    "prompt": prompt,
                    "output_key": output_key,
                    "trigger_payload": trigger_payload,
                },
                started_at=timezone.now(),
            )
            execution.agent_run = agent_run
            execution.save(update_fields=["agent_run", "updated_at", "version"])

        task = AgentTask.objects.filter(agent_run=agent_run, deleted_at__isnull=True).first()
        if task is None:
            task = AgentTask.objects.create(
                organization_id=organization_id,
                team_id=workload.registered_app.team_id,
                project_id=workload.registered_app.project_id,
                agent_definition=workload,
                environment_spec=environment_spec,
                agent_run=agent_run,
                status=AgentTask.Status.DRAFT,
                timeout_seconds=int(stage.timeout_seconds),
                dispatch_input=trigger_payload or None,
            )

    from astrolift_agents.services.task_target import (
        freeze_task_target,
        resolve_task_target,
        task_control_lock,
    )

    with task_control_lock(task.pk):
        task.refresh_from_db()
        execution.refresh_from_db()
        run.refresh_from_db()
        if execution.is_terminal or run.status != "running" or run.ended_at is not None:
            raise RuntimeError(f"stage execution {execution_id} belongs to a closed workflow or stage")
        if task.status in AgentTask.TERMINAL_STATUSES or task.status == AgentTask.Status.RUNNING:
            return str(agent_run.pk)
        from astrolift_agents.services.task_preparation import (
            prepare_agent_task,
            settle_preparation_failure,
        )

        if task.status == AgentTask.Status.DRAFT:
            try:
                prepare_agent_task(
                    task,
                    context={
                        "trigger": "workflow",
                        "workflow_stage_id": str(stage.pk),
                        "workflow_stage_order": stage.order,
                    },
                    skill_refs=skill_refs,
                    prompt=prompt,
                    output_key=output_key,
                )
            except Exception as exc:  # noqa: BLE001
                settle_preparation_failure(task, exc)
                agent_run.status = AgentRun.Status.FAILED
                agent_run.ended_at = timezone.now()
                agent_run.output = {"package_error": str(exc)}
                agent_run.save(update_fields=["status", "ended_at", "output", "updated_at", "version"])
                raise RuntimeError(f"agent package preparation failed for stage {stage_id}: {exc}") from exc
            task.transition_to(AgentTask.Status.QUEUED)

        dispatcher = _resolve_dispatcher_sync(organization_id)
        try:
            if task.dispatch_target:
                backend, cluster, namespace = resolve_task_target(task)
            else:
                backend, cluster = _dispatch_target_sync(run.organization, dispatcher)
        except Exception as exc:  # noqa: BLE001 — a stage that cannot dispatch must settle visibly
            # This used to leave the AgentRun PENDING and return, on the
            # theory that a dispatcher registration might be in flight. On an
            # install where none is ever registered that read as the run
            # hanging in ``running`` forever, with the only trace a WARNING in
            # the worker log (#1704). A stage that cannot dispatch fails the
            # run with the reason attached.
            task.failure = {"message": f"no dispatch target: {exc}"}
            task.save(update_fields=["failure", "updated_at", "version"])
            task.transition_to(AgentTask.Status.FAILED)
            agent_run.status = AgentRun.Status.FAILED
            agent_run.ended_at = timezone.now()
            agent_run.output = {"dispatch_error": str(exc)}
            agent_run.save(update_fields=["status", "ended_at", "output", "updated_at", "version"])
            raise RuntimeError(f"no dispatch target for stage {stage_id}: {exc}") from exc
        if dispatcher is None:
            log.info(
                "dispatch_agent_for_stage: no ACTIVE dispatcher for org=%s; "
                "spawning through the direct path (backend=%s cluster=%s)",
                organization_id,
                backend,
                getattr(cluster, "slug", None),
            )

        if task.status == AgentTask.Status.QUEUED:
            task.transition_to(AgentTask.Status.PROVISIONING)
        elif task.status != AgentTask.Status.PROVISIONING:
            raise RuntimeError(f"agent task {task.guid} cannot resume dispatch from {task.status}")

        # Spawn into the per-org agent namespace (the same one execute_agent_stage
        # uses) and freeze it on the task. Previously this path took the spawner's
        # "default" namespace while the log resolver read the per-org namespace, so
        # agentTaskLogs always came back empty for stage-dispatched agents (#891).
        from astrolift_workflows.activities.agent_stage import _agent_namespace

        namespace = task.dispatch_target.get("namespace") or _agent_namespace(run.organization.slug)
        backend, cluster, namespace = freeze_task_target(
            task, backend=backend, cluster=cluster, namespace=namespace
        )
        task.dispatcher = dispatcher
        task.namespace = namespace
        task.save(update_fields=["dispatcher", "namespace", "updated_at", "version"])

        # A prior attempt may have persisted the external id before losing its
        # activity response. Resume the state transition without spawning again.
        if task.external_id:
            task.pod_name = task.pod_name or task.external_id
            task.save(update_fields=["pod_name", "updated_at", "version"])
            task.transition_to(AgentTask.Status.RUNNING)
            agent_run.status = AgentRun.Status.RUNNING
            agent_run.k8s_pod_name = task.external_id
            agent_run.save(update_fields=["status", "k8s_pod_name", "updated_at", "version"])
            return str(agent_run.pk)

        spawner = get_spawner(backend, cluster=cluster, namespace=namespace)
        try:
            result = spawner.spawn(task)
        except Exception as exc:  # noqa: BLE001 — fail this attempt; workflow policy may retry
            task.failure = {"message": f"spawn raised: {exc}"}
            task.save(update_fields=["failure", "updated_at", "version"])
            task.transition_to(AgentTask.Status.FAILED)
            agent_run.status = AgentRun.Status.FAILED
            agent_run.ended_at = timezone.now()
            agent_run.output = {"spawn_error": str(exc)}
            agent_run.save(update_fields=["status", "ended_at", "output", "updated_at", "version"])
            raise RuntimeError(
                f"spawn failed for stage {stage_id} via {_dispatch_label(dispatcher, backend)}: {exc}"
            ) from exc
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
                f"spawn failed for stage {stage_id} via {_dispatch_label(dispatcher, backend)}: {result.error}"
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

    from astrolift_agents.models import AgentTask, resolve_agent_task_for_run
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

    # Find the dispatched task via the explicit FK, falling back to the
    # historical (workload, external_id == k8s_pod_name) join for rows that
    # predate the FK or push-mode runs (#1217). Backfill the FK once when the
    # fuzzy join resolves so subsequent lookups (and the interaction bridge)
    # go straight through the link.
    task = resolve_agent_task_for_run(agent_run)
    if task is not None and task.agent_run_id is None:
        task.agent_run = agent_run
        task.save(update_fields=["agent_run", "updated_at", "version"])
    if task is not None and task.status in AgentTask.TERMINAL_STATUSES:
        if task.status == AgentTask.Status.COMPLETED:
            agent_run.status = AgentRun.Status.SUCCEEDED
            agent_run.output = task.result or {"exit_code": 0}
        elif task.status == AgentTask.Status.CANCELLED:
            agent_run.status = AgentRun.Status.CANCELLED
            agent_run.output = task.failure
        else:
            agent_run.status = AgentRun.Status.FAILED
            agent_run.output = task.failure
        agent_run.ended_at = task.ended_at or timezone.now()
        agent_run.save(update_fields=["status", "ended_at", "output", "updated_at", "version"])
        return agent_run.status
    if task is None or not task.external_id:
        # Nothing to poll (push-mode only, or nothing spawned). Leave as-is.
        return agent_run.status

    # A task spawned through the no-dispatcher path carries no dispatcher
    # row, and bailing on that left the run reconciling never -- the same
    # hang from the other end (#1704).
    dispatcher = task.dispatcher
    # Spawn freezes the per-org namespace on the task. Poll the same location;
    # the registry default is the literal ``default`` namespace, where this
    # Job does not exist. A status lookup there is reported as failed and
    # terminalizes the task, which also revokes its callback token while the
    # real pod is still running.
    namespace = task.namespace
    if not namespace:
        from astrolift_workflows.activities.agent_stage import _agent_namespace

        namespace = _agent_namespace(task.organization.slug)
    try:
        if task.dispatch_target:
            from astrolift_agents.services.task_target import resolve_task_target

            backend, cluster, namespace = resolve_task_target(task)
        else:
            backend, cluster = _dispatch_target_sync(task.organization, dispatcher)
    except Exception as exc:  # noqa: BLE001 — treat as a transient poll failure
        log.warning("poll_agent_run_status: no dispatch target for %s: %s", agent_run_id, exc)
        return agent_run.status
    spawner = get_spawner(
        backend,
        cluster=cluster,
        namespace=namespace,
    )
    try:
        status = spawner.status(task.external_id)
    except Exception as exc:  # noqa: BLE001 — treat poll failures as transient
        log.warning("poll_agent_run_status: status() failed for %s: %s", agent_run_id, exc)
        return agent_run.status

    if status.succeeded:
        if task.status == AgentTask.Status.RUNNING:
            if task.result is None:
                task.result = {"exit_code": status.exit_code or 0}
                task.save(update_fields=["result", "updated_at", "version"])
            task.transition_to(AgentTask.Status.COMPLETED)
        agent_run.status = AgentRun.Status.SUCCEEDED
        agent_run.ended_at = timezone.now()
        agent_run.output = task.result or {"exit_code": status.exit_code or 0}
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


def _load_agent_run_outcome_sync(agent_run_id: str) -> dict:
    """Return the semantic callback result plus durable run metadata."""
    from astrolift_agents.models import resolve_agent_task_for_run
    from astrolift_lifecycle.models import AgentRun

    agent_run = AgentRun.objects.get(pk=int(agent_run_id))
    task = resolve_agent_task_for_run(agent_run)
    result = task.result if task is not None and task.result is not None else agent_run.output
    failure = task.failure if task is not None and task.failure is not None else None
    return {
        "agent_run_id": str(agent_run.pk),
        "status": agent_run.status,
        "result": result,
        "failure": failure,
        "task_guid": str(task.guid) if task is not None else None,
    }


def _resolve_gate_agent_tasks_sync(execution) -> tuple[list, bool]:
    """Resolve the AgentTask(s) a human-gate execution governs (#1217).

    A gate has no agent dispatch of its own, so it is attributed to the
    agent stage it gates: the AGENT_DISPATCH execution(s) in the same
    ``WorkflowRun`` with the greatest stage ``order`` strictly *below* the
    gate's order — the immediately-preceding agent stage (and every fan-out
    branch of it, since fan-out branch executions share the parent run). When
    no preceding agent stage resolves (e.g. a leading gate) it falls back to
    every resolvable agent task in the run and flags the result ``ambiguous``
    so the caller can note the imprecision.

    Returns ``(tasks, ambiguous)`` with tasks de-duplicated by pk. Attribution
    stays inside the gate's own WorkflowRun, so it can never cross tenants.
    """
    from astrolift_agents.models import resolve_agent_task_for_run
    from workflows.models import WorkflowStage, WorkflowStageExecution

    gate_order = execution.stage.order
    agent_execs = list(
        WorkflowStageExecution.objects.filter(
            workflow_run_id=execution.workflow_run_id,
            agent_run__isnull=False,
            stage__kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        ).select_related("stage", "agent_run")
    )
    below = [(e.stage.order, e.agent_run) for e in agent_execs if e.stage.order < gate_order]
    if below:
        max_order = max(order for order, _ in below)
        target_runs = [run for order, run in below if order == max_order]
        ambiguous = False
    else:
        target_runs = [e.agent_run for e in agent_execs]
        ambiguous = True

    tasks: dict = {}
    for run in target_runs:
        task = resolve_agent_task_for_run(run)
        if task is not None:
            tasks[task.pk] = task
    return list(tasks.values()), ambiguous


def _capture_gate_interaction(
    execution,
    *,
    status: str,
    decided_by_user_id: int | None = None,
    note: str = "",
) -> None:
    """Emit a GATE :class:`AgentInteraction` for a human-gate execution,
    attributed to the agent task(s) the gate governs (#1217).

    Fully defensive: capture is additive / side-effect-only, so any failure
    here (attribution or write) is logged and swallowed and must never break
    the gate-open / decision flow.
    """
    try:
        from astrolift_agents.models import AgentInteraction, record_interaction

        tasks, ambiguous = _resolve_gate_agent_tasks_sync(execution)
        if not tasks:
            return
        stage = execution.stage
        name = stage.name or stage.role or f"human_gate #{stage.order}"
        detail = {
            "execution_id": str(execution.pk),
            "workflow_run_id": str(execution.workflow_run_id),
            "stage_order": stage.order,
            "attributed_task_count": len(tasks),
        }
        if ambiguous:
            # No single preceding agent stage — attributed to every agent task
            # in the run (see _resolve_gate_agent_tasks_sync).
            detail["attribution"] = "all_agent_tasks_in_run"
        if decided_by_user_id is not None:
            detail["decided_by_user_id"] = decided_by_user_id
        if note:
            detail["note"] = note
        for task in tasks:
            record_interaction(
                task,
                kind=AgentInteraction.Kind.GATE,
                name=name,
                status=status,
                detail=detail,
            )
    except Exception:  # noqa: BLE001 — capture must never break the gate flow
        log.exception(
            "failed to capture gate interaction for execution %s",
            getattr(execution, "pk", None),
        )


def _notify_gate_reviewers(run, stage) -> None:
    """Notify the reviewers of a just-opened human gate (#59).

    Translates the ``WorkflowStage`` row into the stage descriptor the
    notification service takes, then hands off. ``approvers`` holds opaque
    approver references (team / role slugs); an entry that is already an
    address is the stage-level assignee, and anything else falls through to
    the service's definition / org-admin resolution.

    Fully defensive, like gate capture: notification is side-effect-only, so
    a failure here must never fail the activity and stall the run.
    """
    try:
        from astrolift_agents.services.human_gate import notify_human_gate

        approvers = stage.approvers or []
        assignee = next((a for a in approvers if isinstance(a, str) and "@" in a), None)
        notify_human_gate(
            run,
            {
                "name": stage.slug or f"stage-{stage.order}",
                "label": stage.name or stage.role,
                "assignee_email": assignee,
            },
        )
    except Exception:  # noqa: BLE001 — notification must never break the gate flow
        log.exception(
            "failed to notify reviewers of gate stage %s on workflow_run %s",
            getattr(stage, "pk", None),
            getattr(run, "pk", None),
        )


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

    # Capture the gate outcome for the P3 interaction map, attributed to the
    # agent task(s) the gate governs (#1217). Runs after the durable decision
    # write so a capture failure can never undo the recorded decision.
    _capture_gate_interaction(
        execution,
        status=decision,
        decided_by_user_id=decided_by_user_id,
        note=note or "",
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
    from astrolift_agents.services.workflow_task_cleanup import cleanup_workflow_tasks

    _finalize_workflow_run_records(workflow_run_id, status, result, failure)
    if ":fanout:" not in str(workflow_run_id):
        cleanup_workflow_tasks(_parent_run_pk(workflow_run_id))


def _finalize_workflow_run_records(
    workflow_run_id: str,
    status: str,
    result: dict | None,
    failure: dict | None,
) -> None:
    """Settle the run, open stage records, and its exact configured instance."""
    from django.db import transaction
    from django.utils import timezone

    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStageExecution
    from workflows.run_status import synchronize_workflow_instances

    valid = {c[0] for c in WorkflowRun.Status.choices}
    if status not in valid:
        raise ValueError(f"invalid workflow run status {status!r}")
    # Fan-out children share their parent's stage store, not its lifecycle.
    # Only the parent executor can decide that the whole run has finished.
    if ":fanout:" in str(workflow_run_id):
        return

    with transaction.atomic():
        run = WorkflowRun.objects.select_for_update().get(pk=_parent_run_pk(workflow_run_id))
        if run.status != WorkflowRun.Status.RUNNING and run.ended_at is not None:
            synchronize_workflow_instances(run)
            return
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
        if status in {"cancelled", "terminated", "timed_out", "failed"}:
            for execution in WorkflowStageExecution.objects.select_for_update().filter(
                workflow_run=run,
                status__in=["pending", "running"],
                deleted_at__isnull=True,
            ):
                execution.status = "failed" if status == "failed" else "cancelled"
                execution.ended_at = run.ended_at
                execution.save(update_fields=["status", "ended_at", "updated_at", "version"])
        synchronize_workflow_instances(run)


# ---------------------------------------------------------------------------
# Temporal activity definitions
# ---------------------------------------------------------------------------


@activity.defn(name="astrolift.workflow_stage.get_workflow_stages")
async def get_workflow_stages(params: str | dict) -> dict:
    """Return ``{pattern_kind, stages: [ordered stage dicts]}``."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    if isinstance(params, str):
        return await sync_to_async(_get_workflow_stages_sync)(params)
    return await sync_to_async(_get_workflow_stages_sync)(
        str(params["workflow_definition_slug"]),
        params.get("workflow_run_id"),
        params.get("stage_bindings"),
        params.get("workflow_definition_id"),
        params.get("workflow_ancestry"),
    )


@activity.defn(name="astrolift.workflow_stage.create_nested_workflow_run")
async def create_nested_workflow_run(
    parent_workflow_run_id: str,
    stage_execution_id: str,
    child_definition_id: str,
) -> dict:
    """Create or return a linked nested WorkflowRun mirror."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_create_nested_workflow_run_sync)(
        parent_workflow_run_id,
        stage_execution_id,
        child_definition_id,
    )


@activity.defn(name="astrolift.workflow_stage.record_nested_workflow_start")
async def record_nested_workflow_start(
    child_workflow_run_id: str,
    temporal_run_id: str,
) -> None:
    """Record the child handle's first execution run id for observability."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_record_nested_workflow_start_sync)(
        child_workflow_run_id,
        temporal_run_id,
    )


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
    resolved_config: dict | None = None,
) -> str:
    """Create an AgentRun + dispatch an AgentTask; return the AgentRun pk."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_dispatch_agent_for_stage_sync, thread_sensitive=False)(
        stage_id,
        execution_id,
        trigger_payload,
        resolved_config,
    )


def _poll_agent_run_progress_sync(agent_run_id: str) -> dict:
    from astrolift_agents.models import resolve_agent_task_for_run
    from astrolift_agents.services.task_target import TaskControlBusy, task_control_lock
    from astrolift_agents.services.task_timeout import task_input_wait_seconds
    from astrolift_lifecycle.models import AgentRun
    from astrolift_workflows.activities.agent_stage import _expire_agent_task

    run = AgentRun.objects.get(pk=int(agent_run_id))
    task = resolve_agent_task_for_run(run)
    if task is not None and task.dispatch_target:
        try:
            with task_control_lock(task.pk):
                _expire_agent_task(task.pk)
        except TaskControlBusy:
            pass
    status = _poll_agent_run_status_sync(agent_run_id)
    if task is not None:
        task.refresh_from_db()
    return {"status": status, "input_wait_seconds": task_input_wait_seconds(task) if task else 0.0}


@activity.defn(name="astrolift.workflow_stage.poll_agent_run_progress")
async def poll_agent_run_progress(agent_run_id: str) -> dict:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_poll_agent_run_progress_sync)(agent_run_id)


@activity.defn(name="astrolift.workflow_stage.poll_agent_run_status")
async def poll_agent_run_status(agent_run_id: str) -> str:
    """Return the current AgentRun status, reconciling the dispatched task."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_poll_agent_run_status_sync)(agent_run_id)


@activity.defn(name="astrolift.workflow_stage.load_agent_run_outcome")
async def load_agent_run_outcome(agent_run_id: str) -> dict:
    """Load the terminal AgentTask callback result for stage chaining."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_load_agent_run_outcome_sync)(agent_run_id)


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
