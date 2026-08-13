from django.db import migrations


def repair_repository_workflow_projects(apps, schema_editor):
    """Retry the 0010 backfill with model ordering cleared before DISTINCT."""

    WorkflowDefinition = apps.get_model("workflows", "WorkflowDefinition")
    WorkflowStage = apps.get_model("workflows", "WorkflowStage")
    Project = apps.get_model("astrolift_identity", "Project")

    definitions = WorkflowDefinition.objects.filter(
        project_id__isnull=True,
        organization_id__isnull=False,
        source_repo__gt="",
        deleted_at__isnull=True,
    )
    for definition in definitions.iterator():
        project_ids = list(
            WorkflowStage.objects.filter(
                definition_id=definition.pk,
                deleted_at__isnull=True,
                agent_definition__registered_app__project_id__isnull=False,
            )
            .order_by()
            .values_list("agent_definition__registered_app__project_id", flat=True)
            .distinct()[:2]
        )
        if len(project_ids) != 1:
            continue
        if not Project.objects.filter(
            pk=project_ids[0],
            organization_id=definition.organization_id,
            deleted_at__isnull=True,
        ).exists():
            continue
        WorkflowDefinition.objects.filter(pk=definition.pk).update(project_id=project_ids[0])


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0010_historicalworkflowdefinition_project_and_more"),
    ]

    operations = [
        migrations.RunPython(
            repair_repository_workflow_projects,
            migrations.RunPython.noop,
        ),
    ]
