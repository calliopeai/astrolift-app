# Hand-written for #731 — cached TLS cert observability metadata on
# CustomDomain so the AppDomain GraphQL type can surface 'expires in
# N days' without a per-request round-trip to the cloud's cert API.
# All columns default null / empty so existing rows backfill cleanly.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0014_deployment_pr_provenance"),
    ]

    operations = [
        migrations.AddField(
            model_name="customdomain",
            name="cert_expires_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="customdomain",
            name="cert_issuer_serial",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="customdomain",
            name="cert_observability_status",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="customdomain",
            name="cert_metadata_refreshed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
