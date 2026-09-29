from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0036_resync_system_roles_edge_access"),
    ]

    operations = [
        migrations.AddField(
            model_name="member",
            name="idp_groups",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="member",
            name="idp_groups_synced_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
