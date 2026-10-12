"""Fail deployments whose deploy workflow is gone (#2184).

A deployment's ``DeployAppWorkflow`` marks it running or failed. When the
workflow dies first (a heartbeat timeout, a worker lost mid-build), the row
stays ``pending``, ``deploying`` or ``redeploying`` until the next deploy of
that environment supersedes it, and the app reads as deploying meanwhile.

Each tick of the run-status reconciler looks at in-flight deployments not
updated for ``STALE_AFTER`` and asks Temporal for the latest run of their
workflow id. The deployment is failed when that run is closed or never
existed, or when it is running but is not the run this deployment bound to.
A running workflow with no bound run yet, an engine that is off or does not
answer, leave the row as it is.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from astrolift_lifecycle.models import Deployment, DeploymentExecutionReceipt

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=15)
"""Longer than a deployment waits between being created and its workflow binding."""

_BUDGET_SECONDS = 30.0
"""Each row costs one Temporal describe and the tick is bounded at 50 s; rows
past the budget wait for the next tick."""

_IN_FLIGHT = (
    Deployment.Status.PENDING.value,
    Deployment.Status.DEPLOYING.value,
    Deployment.Status.REDEPLOYING.value,
)

REASON = "the deploy workflow ended without finishing this deployment"


def _workflow_id(deployment: Deployment) -> str:
    return f"DeployAppWorkflow-{deployment.registered_app.guid}-{deployment.app_environment.guid}"


def _orphaned(deployment: Deployment) -> bool:
    from astrolift_workflows.client import workflow_run_state

    state = workflow_run_state(_workflow_id(deployment))
    if state is None:
        return False
    if not state.running:
        return True
    bound = _bound_run_id(deployment)
    return bool(bound) and bound != state.run_id


def _bound_run_id(deployment: Deployment) -> str:
    """The Temporal run this deployment's workflow bound to, once it started."""
    return (
        DeploymentExecutionReceipt.all_objects.filter(
            deployment=deployment, kind=DeploymentExecutionReceipt.Kind.BOUND
        )
        .values_list("run_id", flat=True)
        .first()
        or ""
    )


def sweep_stuck_deployments(*, now=None) -> int:
    """Fail the in-flight deployments whose workflow is gone. Returns how many."""
    cutoff = (now or timezone.now()) - STALE_AFTER
    rows = (
        Deployment.objects.filter(status__in=_IN_FLIGHT, updated_at__lt=cutoff)
        .select_related("registered_app", "app_environment")
        .order_by("updated_at")
    )
    failed = 0
    started = time.monotonic()
    for deployment in rows:
        if time.monotonic() - started > _BUDGET_SECONDS:
            break
        try:
            if not _orphaned(deployment):
                continue
            with transaction.atomic():
                if not deployment.aborted_reason:
                    deployment.aborted_reason = REASON
                    deployment.save(update_fields=["aborted_reason", "updated_at", "version"])
                deployment.transition_to(Deployment.Status.FAILED)
            failed += 1
            log.warning("deployment sweep: failed deployment %s: %s", deployment.guid, deployment.aborted_reason)
        except Exception:  # noqa: BLE001 — one row must not stop the sweep
            log.exception("deployment sweep: could not settle deployment %s", deployment.pk)
    return failed


__all__ = ["REASON", "STALE_AFTER", "sweep_stuck_deployments"]
