"""Record who and what started every agent task (#2152).

Backfill, where the data allows: the initiator from ``created_by`` (the
requester every dispatch path that knew one already stored), and
``parent`` for a task linked to a workflow stage's ``AgentRun``. Nothing
else on an old row says how it started, so the rest stay ``unknown``.
"""

from django.conf import settings
from django.db import migrations, models
from django.db.models import F


def backfill(apps, schema_editor):
    AgentTask = apps.get_model("astrolift_agents", "AgentTask")
    AgentTask.objects.filter(created_by__isnull=False, triggered_by_user__isnull=True).update(
        triggered_by_user=F("created_by")
    )
    AgentTask.objects.filter(agent_run__isnull=False, trigger_kind="unknown").update(trigger_kind="parent")


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_agents", "0035_agenttask_client_request_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="agenttask",
            name="triggered_by_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="triggered_agent_tasks",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="agenttask",
            name="trigger_kind",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("api", "Api"),
                    ("schedule", "Schedule"),
                    ("webhook", "Webhook"),
                    ("parent", "Parent"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                max_length=16,
            ),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
