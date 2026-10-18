from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("astrolift_registry", "0048_temporary_apps")]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="expiry_cleanup",
            field=models.JSONField(blank=True, default=list, editable=False),
        ),
    ]
