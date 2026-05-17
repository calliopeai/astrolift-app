"""Admin mutations for the Temporal workflow viewer (#437).

Cancel / terminate / signal are admin-gated because they can wedge
real production workflows (mid-deploy, mid-migration). The viewer's
read surfaces are scoped to ``AUDIT_LOG_READ``; these writes layer
``ADMIN_ELEVATE`` on top so an operator with read access doesn't
accidentally fire a terminate.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_workflows.client import (
    cancel_workflow,
    signal_workflow,
    terminate_workflow,
)
from core.permissions import Permission, require_permission
from core.schema.common import MutationResult, ValidationError

JSON = strawberry.scalars.JSON


def _failure(field: str, msg: str) -> MutationResult:
    return MutationResult(ok=False, errors=[ValidationError(field=field, messages=[msg])])


@strawberry.type
class TemporalWorkflowsMutation:
    @strawberry.mutation
    @require_permission(Permission.AUDIT_LOG_READ, Permission.ADMIN_ELEVATE)
    def cancel_workflow_instance(
        self,
        info: Info,
        workflow_id: str,
    ) -> MutationResult:
        """Cooperative cancel — Temporal signals the workflow which can
        run cleanup before exiting. Use this for workflows that own
        external resources (deploys, migrations) so they teardown
        cleanly. Returns ``ok=False`` with a non-empty errors list when
        Temporal is disabled or the handle is missing."""
        if not workflow_id:
            return _failure("workflow_id", "workflow_id is required")
        delivered = cancel_workflow(workflow_id)
        if not delivered:
            return _failure("workflow_id", "cancel could not be delivered")
        return MutationResult.success()

    @strawberry.mutation
    @require_permission(Permission.AUDIT_LOG_READ, Permission.ADMIN_ELEVATE)
    def terminate_workflow_instance(
        self,
        info: Info,
        workflow_id: str,
        reason: str,
    ) -> MutationResult:
        """Hard terminate — Temporal kills the workflow immediately,
        no cleanup runs. Reserve for wedged workflows that the
        cooperative cancel can't unstick. ``reason`` is required and
        stored on the Temporal record so the next operator sees why."""
        if not workflow_id:
            return _failure("workflow_id", "workflow_id is required")
        reason = (reason or "").strip()
        if not reason:
            return _failure("reason", "reason is required")
        delivered = terminate_workflow(workflow_id, reason)
        if not delivered:
            return _failure("workflow_id", "terminate could not be delivered")
        return MutationResult.success()

    @strawberry.mutation
    @require_permission(Permission.AUDIT_LOG_READ, Permission.ADMIN_ELEVATE)
    def signal_workflow_instance(
        self,
        info: Info,
        workflow_id: str,
        signal_name: str,
        payload: JSON | None = None,
    ) -> MutationResult:
        """Send an arbitrary signal to a running workflow.

        Power-user escape hatch for workflows that expose custom
        signals (``abort``, ``cancel_teardown``, etc.). Payload is
        passed through as the single signal argument; pass ``null`` for
        signals that take no args. Returns ``ok=False`` when Temporal
        is disabled or the signal couldn't be delivered (workflow
        already complete, handle missing)."""
        if not workflow_id:
            return _failure("workflow_id", "workflow_id is required")
        signal_name = (signal_name or "").strip()
        if not signal_name:
            return _failure("signal_name", "signal_name is required")
        args: list = [payload] if payload is not None else []
        delivered = signal_workflow(workflow_id, signal_name, *args)
        if not delivered:
            return _failure("signal_name", "signal could not be delivered")
        return MutationResult.success()
