# Hand-written for #737 — compareDeployments needs a manifest snapshot
# captured at deploy time so the comparison query can diff two deploys
# without re-rendering. Null on pre-existing rows; comparison falls back
# to empty dict for those.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0019_domain_path_route"),
    ]

    operations = [
        migrations.AddField(
            model_name="deployment",
            name="rendered_manifest_snapshot",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
