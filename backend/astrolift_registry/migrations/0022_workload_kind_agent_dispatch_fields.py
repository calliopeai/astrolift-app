"""Add agent to Workload.Kind and the agent dispatch tuning fields.

Workload.Kind gains AGENT = "agent" — the long-running AI-agent
primitive added alongside the manifest parser / renderer in the
runtime-primitives effort (#792, #795). The agent kind renders to the
same K8s shape as a deployment (Deployment + Service + HPA) plus a
``astrolift.dev/workload-kind: agent`` pod annotation and injected
``ASTROLIFT_*`` dispatch env vars.

Workload also gains three agent dispatch fields — ``max_retries``,
``tool_timeout_seconds``, ``result_ttl_hours`` — used by the renderer to
populate the agent pod's environment and (for ``result_ttl_hours``) by
the AgentRun retention path (#804).

All changes are additive: ``kind`` only gains a choice (no data change),
and the three new columns ship with defaults matching the manifest spec,
so every existing Workload row stays valid as a no-op.

Note: migration 0021 was scoped (in #805's title) to add TASK / AGENT /
WORKFLOW / FUNCTION but only landed TASK. This migration adds AGENT;
WORKFLOW / FUNCTION arrive with their own renderer PRs (#796 / #794).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0021_workload_kind_task_app_source_kind_direct_upload"),
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
                ],
                default="deployment",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="workload",
            name="max_retries",
            field=models.PositiveIntegerField(default=5),
        ),
        migrations.AddField(
            model_name="workload",
            name="tool_timeout_seconds",
            field=models.PositiveIntegerField(default=300),
        ),
        migrations.AddField(
            model_name="workload",
            name="result_ttl_hours",
            field=models.PositiveIntegerField(default=72),
        ),
    ]
