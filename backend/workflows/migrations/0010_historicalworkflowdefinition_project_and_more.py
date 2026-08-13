import django.db.models.deletion
from django.db import migrations, models


def backfill_repository_workflow_projects(apps, schema_editor):
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
        ("astrolift_identity", "0020_resync_system_roles_agent_env_spec"),
        ("workflows", "0009_workflowstage_agent_runtime_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalworkflowdefinition",
            name="project",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text="Project packet that owns this repository workflow; null for reusable templates.",
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="astrolift_identity.project",
            ),
        ),
        migrations.AddField(
            model_name="workflowdefinition",
            name="project",
            field=models.ForeignKey(
                blank=True,
                help_text="Project packet that owns this repository workflow; null for reusable templates.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="workflow_definitions",
                to="astrolift_identity.project",
            ),
        ),
        migrations.RunPython(
            backfill_repository_workflow_projects,
            migrations.RunPython.noop,
        ),
    ]
