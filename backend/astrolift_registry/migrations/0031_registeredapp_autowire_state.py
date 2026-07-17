# Generated for #1108 — persisted autowire outcome snapshot.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0030_alter_registeredapp_provisioning_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="autowire_state",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
