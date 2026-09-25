"""Synchronize configured-run records from their exact execution mirror."""

from django.db import transaction
from django.db.models import Q


@transaction.atomic
def synchronize_workflow_instances(run, *, authoritative: bool = False) -> bool:
    from workflows.models import WorkflowInstance

    if not run.workflow_id or not run.organization_id:
        return False
    if not authoritative and (run.status == "running" or run.ended_at is None):
        return False
    instances = WorkflowInstance.objects.select_for_update().filter(
        organization_id=run.organization_id,
        temporal_workflow_id=run.workflow_id,
        deleted_at__isnull=True,
    )
    if not authoritative:
        instances = instances.filter(completed_at__isnull=True)
    if run.run_id:
        # An instance recorded before its run id was known (the
        # runWorkflowDefinition mirror, #1774) is this run's: same org, same
        # workflow id, no run id yet. It is claimed and stamped below.
        instances = instances.filter(
            Q(temporal_run_id=run.run_id) | Q(temporal_run_id="") | Q(temporal_run_id__isnull=True)
        )
    else:
        instances = instances.filter(Q(temporal_run_id="") | Q(temporal_run_id__isnull=True))
    changed = False
    for instance in instances:
        claim = bool(run.run_id) and not instance.temporal_run_id
        if not claim and instance.current_state == run.status and instance.completed_at == run.ended_at:
            continue
        changed = True
        instance.current_state = run.status
        instance.completed_at = run.ended_at
        fields = ["current_state", "completed_at", "updated_at", "version"]
        if claim:
            instance.temporal_run_id = run.run_id
            fields.append("temporal_run_id")
        instance.save(update_fields=fields)
    return changed
