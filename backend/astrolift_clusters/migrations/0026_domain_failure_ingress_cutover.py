from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_clusters", "0025_cloudflare_delegation"),
    ]

    operations = [
        migrations.AddField(
            model_name="manageddomain",
            name="provision_failed_step",
            field=models.CharField(blank=True, default="", editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="manageddomain",
            name="provision_failure_reason",
            field=models.CharField(blank=True, default="", editable=False, max_length=300),
        ),
        migrations.AddField(
            model_name="tenantcluster",
            name="preserved_ingress_releases",
            field=models.JSONField(blank=True, default=list, editable=False),
        ),
        migrations.AddField(
            model_name="tenantcluster",
            name="ingress_cutover_completed_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
