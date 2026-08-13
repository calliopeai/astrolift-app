import django.db.models.deletion
from django.db import migrations, models


def backfill_workflow_definitions(apps, schema_editor):
    WorkflowRun = apps.get_model("astrolift_operations", "WorkflowRun")
    WorkflowInstance = apps.get_model("workflows", "WorkflowInstance")

    runs = WorkflowRun.objects.filter(
        workflow_definition_id__isnull=True,
        workflow_kind="WorkflowDefinitionRunWorkflow",
    )
    for run in runs.iterator():
        definition_ids = list(
            WorkflowInstance.objects.filter(
                temporal_workflow_id=run.workflow_id,
                deleted_at__isnull=True,
                workflow_id__isnull=False,
                workflow__organization_id=run.organization_id,
                workflow__deleted_at__isnull=True,
            )
            .values_list("workflow_id", flat=True)
            .distinct()[:2]
        )
        if len(definition_ids) == 1:
            WorkflowRun.objects.filter(pk=run.pk).update(workflow_definition_id=definition_ids[0])


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0018_app_uptime_result"),
        ("workflows", "0010_historicalworkflowdefinition_project_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowrun",
            name="workflow_definition",
            field=models.ForeignKey(
                blank=True,
                help_text="Definition executed by an agent workflow run; null for non-definition operations.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="execution_runs",
                to="workflows.workflowdefinition",
            ),
        ),
        migrations.RunPython(backfill_workflow_definitions, migrations.RunPython.noop),
    ]
