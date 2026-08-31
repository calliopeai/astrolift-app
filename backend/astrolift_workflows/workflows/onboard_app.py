"""
OnboardAppWorkflow — bring a registered app to provisioning_status=ready.

Mirrors specs/06 §4.1. Uses parallel child workflows for independent
provisioning steps so each component gets its own Temporal history entry
(auditable, individually retriable). Steps that depend on each other run
sequentially; independent steps fan out via asyncio.gather.

Topology (as of the "parallel-child-provision" patch):

    mark_provisioning
    ├── RegistryProvisionWorkflow  ┐ parallel — independent, no ordering
    └── NamespaceProvisionWorkflow ┘ constraint between them
    mark_ready

Idempotent by construction: each activity/child-workflow is idempotent and
the workflow id is ``OnboardAppWorkflow-<app-guid>`` so re-enqueues are
no-ops while a workflow is already in flight.

Progress query
--------------
The workflow exposes ``provisioning_progress`` so the API can surface
live step-by-step status without polling the database:

    result = await temporal_client.query_workflow(
        OnboardAppWorkflow,
        "provisioning_progress",
        id="OnboardAppWorkflow-<guid>",
        run_id="...",
    )
    # {"current_step": "provisioning:registry+namespace",
    #  "completed": ["registry"],
    #  "total_steps": ["registry", "namespace", "ready"]}
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import OnboardAppInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        mark_app_failed,
        mark_app_provisioning,
        mark_app_ready,
        provision_managed_services_initial,
        provision_namespace,
        provision_registry_repo,
    )

_ACTIVITY_TIMEOUT = timedelta(minutes=10)

# Ordered list of logical provisioning steps — used by the query handler
# to let callers compute progress percentage without parsing step names.
_PROVISION_STEPS = ["registry", "namespace", "ready"]


@workflow.defn(name="OnboardAppWorkflow")
class OnboardAppWorkflow:
    """Bring a registered app from pending → provisioning → ready.

    Exposes a ``provisioning_progress`` query so the frontend can stream
    live step-by-step status without polling the database.
    """

    def __init__(self) -> None:
        self._step: str = "starting"
        self._completed: list[str] = []

    @workflow.query
    def provisioning_progress(self) -> dict:
        """Return the current provisioning step and completed steps.

        Return shape::

            {
                "current_step": str,      # e.g. "provisioning:registry+namespace"
                "completed": list[str],   # e.g. ["registry", "namespace"]
                "total_steps": list[str], # always ["registry", "namespace", "ready"]
            }

        Callers query via the Temporal client or the GraphQL
        ``astroliftApp { provisioningProgress { ... } }`` field (to be
        wired in a follow-up ticket).
        """
        return {
            "current_step": self._step,
            "completed": list(self._completed),
            "total_steps": _PROVISION_STEPS,
        }

    @workflow.run
    async def run(self, input: OnboardAppInput) -> WorkflowResult:
        # Record why a provision died before letting it die. Until #1677 a
        # failure left the app at `provisioning` with an empty
        # `provisioning_error` forever, indistinguishable from one still in
        # flight -- and there is no CLI route to the worker's logs, so a tenant
        # had nothing to go on at all.
        #
        # The failure is re-raised, not swallowed: the workflow still fails, so
        # Temporal's retry and history semantics are exactly as before. This
        # only writes down the reason on the way past.
        try:
            return await self._provision(input)
        except Exception as error:
            await workflow.execute_activity(
                mark_app_failed,
                args=[input.registered_app_id, f"{self._step}: {error}"],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )
            raise

    async def _provision(self, input: OnboardAppInput) -> WorkflowResult:
        self._step = "provisioning"
        await workflow.execute_activity(
            mark_app_provisioning,
            input.registered_app_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )

        if workflow.patched("parallel-child-provision"):
            # ── New path (2025-05) ─────────────────────────────────────────
            # Registry + namespace are independent — fan them out in parallel
            # as child workflows. Each child gets its own Temporal history:
            # independently auditable, retriable, and eventually targetable
            # by a RebuildComponentWorkflow without re-running the full onboard.
            #
            # Child workflow IDs are deterministic from the parent's workflow
            # ID so they're easy to look up in the Temporal UI.
            self._step = "provisioning:registry+namespace"
            parent_id = workflow.info().workflow_id
            task_queue = workflow.info().task_queue
            await asyncio.gather(
                workflow.execute_child_workflow(
                    "RegistryProvisionWorkflow",
                    input,
                    id=f"{parent_id}/registry",
                    task_queue=task_queue,
                ),
                workflow.execute_child_workflow(
                    "NamespaceProvisionWorkflow",
                    input,
                    id=f"{parent_id}/namespace",
                    task_queue=task_queue,
                ),
            )
            self._completed.extend(["registry", "namespace"])
        else:
            # ── Old path (replay safety for pre-patch in-flight runs) ──────
            # Keeps the original sequential activity calls so Temporal can
            # replay history events from runs started before this deploy.
            await workflow.execute_activity(
                provision_registry_repo,
                input.registered_app_id,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )
            await workflow.execute_activity(
                provision_namespace,
                input.registered_app_id,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )
            await workflow.execute_activity(
                provision_managed_services_initial,
                args=[input.registered_app_id, 0],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )

        self._step = "ready"
        await workflow.execute_activity(
            mark_app_ready,
            input.registered_app_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
        self._completed.append("ready")
        self._step = "completed"
        return WorkflowResult(ok=True, message="onboarded")
