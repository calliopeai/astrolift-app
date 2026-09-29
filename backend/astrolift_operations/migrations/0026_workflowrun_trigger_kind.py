"""Record what started every workflow run (#2152).

Backfill, where the data allows: ``parent`` for a nested run, the cron
and deploy-token mirrors their tokens name, and a deployment's workflow
from that deployment's own trigger. The person was already on
``trigger_actor_user``. Nothing else on an old row says how it started
(a person's run could have come from the UI or a token), so the rest
stay ``unknown``.
"""

from django.db import migrations, models

_DEPLOY_TRIGGERS = {
    "manual": "manual",
    "rollback": "manual",
    "promotion": "manual",
    "ci": "api",
    "push": "webhook",
    "scheduled": "schedule",
}


def backfill(apps, schema_editor):
    WorkflowRun = apps.get_model("astrolift_operations", "WorkflowRun")
    unknown = WorkflowRun.objects.filter(trigger_kind="unknown")
    unknown.filter(parent_run__isnull=False).update(trigger_kind="parent")
    unknown.filter(trigger_actor_token_kind="cron").update(trigger_kind="schedule")
    unknown.filter(trigger_actor_token_kind="deploy_token").update(trigger_kind="api")
    for deploy_trigger, run_trigger in _DEPLOY_TRIGGERS.items():
        WorkflowRun.objects.filter(
            trigger_kind="unknown",
            pk__in=models.Subquery(
                apps.get_model("astrolift_lifecycle", "Deployment")
                .objects.filter(trigger_kind=deploy_trigger, workflow_run__isnull=False)
                .values("workflow_run_id")
            ),
        ).update(trigger_kind=run_trigger)


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0025_zentinelle_connection"),
        ("astrolift_lifecycle", "0039_deployment_secret_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowrun",
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
