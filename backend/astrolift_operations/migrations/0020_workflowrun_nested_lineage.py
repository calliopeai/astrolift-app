import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0019_workflowrun_workflow_definition"),
        ("workflows", "0012_workflowstage_workflow_ref"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowrun",
            name="nesting_depth",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="workflowrun",
            name="parent_run",
            field=models.ForeignKey(
                blank=True,
                help_text="Parent definition run for a nested workflow invocation.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="child_runs",
                to="astrolift_operations.workflowrun",
            ),
        ),
        migrations.AddField(
            model_name="workflowrun",
            name="parent_stage_execution",
            field=models.OneToOneField(
                blank=True,
                help_text="Parent stage execution that invoked this nested workflow run.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="child_workflow_run",
                to="workflows.workflowstageexecution",
            ),
        ),
    ]
