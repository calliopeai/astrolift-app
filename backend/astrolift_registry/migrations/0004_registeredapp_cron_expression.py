from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_registry", "0003_registeredapp_manifest_staging_and_anchor"),
    ]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="cron_expression",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AlterField(
            model_name="registeredapp",
            name="trigger_mode",
            field=models.CharField(
                choices=[
                    ("auto_on_push", "Auto On Push"),
                    ("manual", "Manual"),
                    ("external_ci", "External Ci"),
                    ("cron", "Cron"),
                ],
                default="auto_on_push",
                max_length=32,
            ),
        ),
    ]
