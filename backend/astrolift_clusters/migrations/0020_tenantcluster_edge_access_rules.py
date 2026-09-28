from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_clusters", "0019_zone_verification_challenge"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantcluster",
            name="edge_access_rules",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
