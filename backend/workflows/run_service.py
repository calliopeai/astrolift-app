"""Shared entry point for starting a WorkflowDefinition stage-executor run.

Both ``runWorkflowDefinition`` (the GraphQL mutation) and the inbound webhook
trigger must start the SAME executor (``WorkflowDefinitionRunWorkflow``) so a
webhook-fired workflow actually runs its stages — previously the webhook went
through the legacy ``WorkflowInstance`` state-machine path and never executed
the agent stages (#1020).
"""

from __future__ import annotations

from typing import Any


def build_workflow_definition_run_input(
    definition,
    *,
    trigger_payload: dict | None = None,
    organization_id: int | None = None,
    actor: Any = None,
    stage_bindings: dict | None = None,
):
    """Create the ``WorkflowRun`` mirror row and build the executor input.

    Returns ``(workflow_run, run_input, workflow_id)``. Factored out of
    ``start_workflow_definition_run`` so a caller that hands the input to a
    Temporal *schedule* action (rather than starting the run inline) builds it
    the same way — a ``WorkflowDefinitionRunInput`` the executor's ``run``
    deserializes, never a plain dict (#1036). The ``WorkflowRun`` is the
    executor's source of truth (stage executions key on its pk); the Temporal
    workflow id is derived from that pk.
    """
    from django.utils import timezone

    from astrolift_operations.models import WorkflowRun
    from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput

    payload = dict(trigger_payload or {})
    if actor is None:
        actor = Actor(kind="system", user_id=None, display="webhook")

    run = WorkflowRun.objects.create(
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_definition=definition,
        workflow_id="",
        run_id="",
        status=WorkflowRun.Status.RUNNING,
        started_at=timezone.now(),
        organization_id=organization_id,
        trigger_actor_user_id=getattr(actor, "user_id", None),
    )
    workflow_id = f"WorkflowDefinitionRunWorkflow-{run.pk}"
    run.workflow_id = workflow_id
    run.save(update_fields=["workflow_id", "updated_at", "version"])

    run_input = WorkflowDefinitionRunInput(
        workflow_definition_slug=definition.slug,
        workflow_definition_id=str(definition.pk),
        workflow_run_id=str(run.pk),
        trigger_payload=payload,
        actor=actor,
        stage_bindings=dict(stage_bindings) if stage_bindings else None,
    )
    return run, run_input, workflow_id


def start_workflow_definition_run(
    definition,
    *,
    trigger_payload: dict | None = None,
    organization_id: int | None = None,
    actor: Any = None,
    stage_bindings: dict | None = None,
):
    """Create the ``WorkflowRun`` mirror and start the stage executor.

    Returns ``(workflow_run, workflow_id)``. The ``WorkflowRun`` is the
    executor's source of truth (stage executions key on its pk); the Temporal
    workflow id is derived from that pk. ``actor`` is an
    ``astrolift_workflows.inputs.Actor`` (caller for the mutation, a synthetic
    ``system`` actor for a webhook).
    """
    from astrolift_workflows.client import start_workflow

    run, run_input, workflow_id = build_workflow_definition_run_input(
        definition,
        trigger_payload=trigger_payload,
        organization_id=organization_id,
        actor=actor,
        stage_bindings=stage_bindings,
    )

    handle = start_workflow(
        "WorkflowDefinitionRunWorkflow",
        args=[run_input],
        workflow_id=workflow_id,
    )
    if handle.enqueued and handle.run_id:
        run.run_id = handle.run_id
        run.save(update_fields=["run_id", "updated_at", "version"])

    return run, workflow_id
