"""Add volumes JSON field to Workload (#739)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0019_archive_app"),
    ]

    operations = [
        migrations.AddField(
            model_name="workload",
            name="volumes",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
