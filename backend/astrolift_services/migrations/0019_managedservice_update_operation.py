from django.db import migrations, models
from django.db.models import F


def seed_applied_config(apps, schema_editor):
    ManagedService = apps.get_model("astrolift_services", "ManagedService")
    ManagedService.objects.filter(status="active").update(applied_config=F("config"))


def clear_applied_config(apps, schema_editor):
    ManagedService = apps.get_model("astrolift_services", "ManagedService")
    ManagedService.objects.update(applied_config=None)


class Migration(migrations.Migration):
    dependencies = [("astrolift_services", "0018_managedservicevolumebinding_dynamic_pvc")]

    operations = [
        migrations.AddField(
            model_name="managedservice",
            name="applied_config",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="managedservice",
            name="operation_kind",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="managedservice",
            name="operation_workflow_id",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="managedservice",
            name="operation_run_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="managedservice",
            name="operation_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="managedservice",
            name="operation_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(seed_applied_config, clear_applied_config),
    ]
