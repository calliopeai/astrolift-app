from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0035_alter_workload_run_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="manifest_bootstrap_status",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="registeredapp",
            name="manifest_bootstrap_error",
            field=models.TextField(blank=True, default=""),
        ),
    ]
