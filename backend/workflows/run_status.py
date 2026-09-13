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
        instances = instances.filter(temporal_run_id=run.run_id)
    else:
        instances = instances.filter(Q(temporal_run_id="") | Q(temporal_run_id__isnull=True))
    changed = False
    for instance in instances:
        if instance.current_state == run.status and instance.completed_at == run.ended_at:
            continue
        changed = True
        instance.current_state = run.status
        instance.completed_at = run.ended_at
        instance.save(update_fields=["current_state", "completed_at", "updated_at", "version"])
    return changed
