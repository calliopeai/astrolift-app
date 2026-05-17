"""Add ``RegisteredApp.webhook_deploys_paused`` + audit columns (#399).

App-global pause for webhook-fired deploys (push / ci / scheduled trigger
kinds). Independent of the per-env ``deploys_paused`` and
``ingress_paused`` axes — pausing here stops the deploy storm without
locking operators out of a manual rescue.

Additive — no data backfill needed. Existing rows default to ``False``.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0011_workload_concurrency_policy"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="webhook_deploys_paused",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="registeredapp",
            name="webhook_deploys_paused_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="registeredapp",
            name="webhook_deploys_paused_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="webhook_deploys_paused_apps",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="registeredapp",
            name="webhook_deploys_pause_reason",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
    ]
