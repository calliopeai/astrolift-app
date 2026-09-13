"""Synchronize configured-run records from their exact execution mirror."""

from django.db import transaction
from django.db.models import Q


@transaction.atomic
def synchronize_workflow_instances(run) -> None:
    from workflows.models import WorkflowInstance

    if run.status == "running" or run.ended_at is None or not run.workflow_id or not run.organization_id:
        return
    instances = WorkflowInstance.objects.select_for_update().filter(
        organization_id=run.organization_id,
        temporal_workflow_id=run.workflow_id,
        completed_at__isnull=True,
        deleted_at__isnull=True,
    )
    if run.run_id:
        instances = instances.filter(temporal_run_id=run.run_id)
    else:
        instances = instances.filter(Q(temporal_run_id="") | Q(temporal_run_id__isnull=True))
    for instance in instances:
        instance.current_state = run.status
        instance.completed_at = run.ended_at
        instance.save(update_fields=["current_state", "completed_at", "updated_at", "version"])
