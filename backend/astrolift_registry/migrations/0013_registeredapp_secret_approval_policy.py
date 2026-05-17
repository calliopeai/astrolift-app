# Hand-written for #488 — secret-change approval policy on RegisteredApp.
# Mirrors the existing deployment approval gear (requires_approval,
# approver_users, minimum_approvals) introduced in 0005.

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0012_registeredapp_webhook_deploys_paused"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="requires_secret_approval",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="registeredapp",
            name="secret_approver_users",
            field=models.ManyToManyField(
                blank=True,
                related_name="secret_approver_for_apps",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="registeredapp",
            name="secret_minimum_approvals",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
