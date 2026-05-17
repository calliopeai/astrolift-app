"""Apps-list filter indexes (#481).

Supports the server-side filter pivots added to ``astroliftApps`` /
``astroliftMyApps`` (and their page variants) — team_slug, project_slug,
status. Each filter narrows the queryset before the
``(-created_at, -guid)`` cursor seek; the indexes here are the
supporting columns for those narrows on the install's largest table.

Additive — no data backfill needed.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0012_registeredapp_webhook_deploys_paused"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="registeredapp",
            index=models.Index(fields=["team", "deleted_at"], name="app_team_deleted_idx"),
        ),
        migrations.AddIndex(
            model_name="registeredapp",
            index=models.Index(fields=["project", "deleted_at"], name="app_project_deleted_idx"),
        ),
        migrations.AddIndex(
            model_name="registeredapp",
            index=models.Index(
                fields=["provisioning_status"],
                name="app_provisioning_status_idx",
            ),
        ),
    ]
