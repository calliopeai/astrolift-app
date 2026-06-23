from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0009_alter_appsecretmetadata_environment_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="managedservice",
            name="backend_ref",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
    ]
