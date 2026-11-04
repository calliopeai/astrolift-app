from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0048_merge_azure_identity_native_removal"),
    ]

    operations = [
        migrations.AddField(
            model_name="managedservice",
            name="failed_step",
            field=models.CharField(blank=True, default="", db_default="", max_length=32),
        ),
    ]
