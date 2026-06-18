"""Add the agent run-spec fields to Workload (spec 33, PR-1).

The run spec decides *how* an ``agent`` workload runs — the platform's
native Task-vs-Service (Job-vs-Deployment) split plus the Task trigger
mode (Once/Loop/Schedule/Trigger). PR-1 wires only Once dispatch end-to-
end via ``runAstroliftAgent``; the remaining modes, Service replicas, and
scheduled scaling hang off these same columns in later PRs (PR-4/5/6), so
the schema is stable and no further agent run-spec migration is needed.

All changes are additive ``AddField``s with defaults matching a no-op for
every existing row:
  * ``run_family`` defaults ``task`` and ``run_mode`` defaults ``once`` —
    a deployment/job/cronjob row keeps the defaults and ignores them
    (only ``kind == agent`` consults the run spec).
  * the cron / paused / max-parallel / scheduled-scaling columns ship
    blank/null/False, so the migration touches no data.

The scheduled-scaling trio (``scheduled_scale_to`` / ``scale_up_cron`` /
``scale_down_cron``) is declared null here as forward-compatible
placeholders for PR-5; PR-1 neither reads nor writes them.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0027_registeredapp_build_strategy"),
    ]

    operations = [
        migrations.AddField(
            model_name="workload",
            name="run_family",
            field=models.CharField(
                choices=[("task", "Task"), ("service", "Service")],
                default="task",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="workload",
            name="run_mode",
            field=models.CharField(
                choices=[
                    ("once", "Once"),
                    ("loop", "Loop"),
                    ("schedule", "Schedule"),
                    ("trigger", "Trigger"),
                ],
                default="once",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="workload",
            name="run_cron_expression",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="workload",
            name="run_paused",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="workload",
            name="run_max_parallel",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workload",
            name="scheduled_scale_to",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workload",
            name="scale_up_cron",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="workload",
            name="scale_down_cron",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
