# Generated for #736 — Deployment.strategy captures the rollout kind
# (rolling / blue_green / canary / recreate / unknown) decided by the
# Temporal workflow's rollout-policy step. Default ``unknown`` so all
# existing rows backfill cleanly without a data migration; the FE
# renders a neutral pill for unknown.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0015_customdomain_cert_observability"),
    ]

    operations = [
        migrations.AddField(
            model_name="deployment",
            name="strategy",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("rolling", "Rolling"),
                    ("blue_green", "Blue Green"),
                    ("canary", "Canary"),
                    ("recreate", "Recreate"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                blank=True,
            ),
        ),
    ]
