"""Add WorkflowRun.current_stage_execution FK (#46).

Quick-access pointer so operator polling doesn't need to scan all
stage execution rows — just follow the FK. Null for runs that have no
stage-level granularity (top-level single-stage, or not yet started).
SET_NULL on delete so a deleted stage execution doesn't cascade the run.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_operations", "0015_alertrule_managed_service"),
        ("workflows", "0002_workflow_patterns_and_stages"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowrun",
            name="current_stage_execution",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Quick-access pointer to the stage currently executing. "
                    "Null for runs with no stage-level granularity or runs "
                    "that have not yet started stage execution."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="workflows.workflowstageexecution",
            ),
        ),
    ]
