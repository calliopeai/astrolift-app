"""Add ``Workload.concurrency_policy`` (#427).

Surfaces the CronJob concurrency policy on the platform row so the
jobs UI can render an at-a-glance badge instead of forcing operators
to read the K8s manifest. Defaults to ``forbid`` — the platform
renderer's previous hard-coded value, so every existing cronjob keeps
its current behaviour.

Additive — no data backfill needed.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0010_registeredapp_security_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="workload",
            name="concurrency_policy",
            field=models.CharField(
                choices=[
                    ("forbid", "Forbid"),
                    ("queue", "Queue"),
                    ("replace", "Replace"),
                ],
                default="forbid",
                max_length=16,
            ),
        ),
    ]
