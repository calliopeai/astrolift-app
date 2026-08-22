from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0029_previewenvironment_pin"),
    ]

    operations = [
        migrations.AddField(
            model_name="deployment",
            name="manifest_resync_status",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="deployment",
            name="manifest_resync_error",
            field=models.TextField(blank=True, default=""),
        ),
    ]
