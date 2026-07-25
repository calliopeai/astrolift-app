# Generated for #1209 — managed CI-workflow sync record on RegisteredApp.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0031_registeredapp_autowire_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="ci_workflow_state",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="registeredapp",
            name="ci_workflow_template_version",
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
    ]
