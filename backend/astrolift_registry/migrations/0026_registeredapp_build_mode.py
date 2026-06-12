"""Add build_mode field to RegisteredApp (#867).

Additive CharField with default "off" so no existing rows need
backfilling and the migration is non-blocking on Postgres.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_registry", "0025_workload_agent_variant_runtime"),
    ]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="build_mode",
            field=models.CharField(
                choices=[
                    ("off", "Off"),
                    ("dockerfile", "Dockerfile"),
                    ("buildpacks", "Buildpacks"),
                    ("nixpacks", "Nixpacks"),
                ],
                default="off",
                max_length=32,
            ),
        ),
    ]
