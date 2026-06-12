"""Add api_key_hash + last_heartbeat_at to TenantCluster (#808)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_clusters", "0010_tenantcluster_alb_auth_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantcluster",
            name="api_key_hash",
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="tenantcluster",
            name="last_heartbeat_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
