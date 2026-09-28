from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0041_populate_hostname_claims"),
    ]

    operations = [
        migrations.AddField(
            model_name="registeredapp",
            name="edge_access",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
