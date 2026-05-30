"""Add FUNCTION = 'function' to Workload.Kind.

Additive — no data migration, no column type change. Follows the same
pattern as 0021 (TASK) and 0022 (AGENT/WORKFLOW).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_registry", "0023_alter_workload_kind"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workload",
            name="kind",
            field=models.CharField(
                choices=[
                    ("deployment", "Deployment"),
                    ("statefulset", "Statefulset"),
                    ("job", "Job"),
                    ("cronjob", "Cronjob"),
                    ("task", "Task"),
                    ("agent", "Agent"),
                    ("workflow", "Workflow"),
                    ("function", "Function"),
                ],
                default="deployment",
                max_length=32,
            ),
        ),
    ]
