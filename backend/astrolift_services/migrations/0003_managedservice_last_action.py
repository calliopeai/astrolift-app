# Hand-written for #401 — per-service quick-action timestamp + kind
# cache so the Settings landing summary can render "tested 3m ago"
# without re-walking the platform audit log.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0002_alter_managedservice_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="managedservice",
            name="last_action_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="managedservice",
            name="last_action_kind",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
