# Generated for #390 — manual ScheduledJobRun rows record trigger_kind +
# triggered_by + namespace so an operator-issued run-once can be
# distinguished from a controller-issued scheduled run on the existing
# scheduled-job-runs surface.

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0009_appenvironment_ingress_paused"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduledjobrun",
            name="namespace",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="scheduledjobrun",
            name="trigger_kind",
            field=models.CharField(
                choices=[("scheduled", "Scheduled"), ("manual", "Manual")],
                default="scheduled",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="scheduledjobrun",
            name="triggered_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="triggered_scheduled_job_runs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
