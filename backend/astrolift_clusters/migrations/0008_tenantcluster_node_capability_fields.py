# Generated migration — node pool platform capability fields (#80).
# Adds node_os, node_arch, and node_labels to TenantCluster so the
# dispatch router (#82) can match jobs' runs_on selectors against
# what each cluster actually supports.
#
# Defaults: node_os="linux", node_arch="", node_labels=[]
# These are safe-guess values for all existing clusters; operators can
# update via mutation after the migration runs.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_clusters", "0007_clusterbootstraprun"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantcluster",
            name="node_os",
            field=models.CharField(
                blank=True,
                choices=[
                    ("linux", "Linux"),
                    ("windows", "Windows"),
                    ("macos", "Macos"),
                ],
                default="linux",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="tenantcluster",
            name="node_arch",
            field=models.CharField(
                blank=True,
                choices=[
                    ("amd64", "Amd64"),
                    ("arm64", "Arm64"),
                ],
                default="",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="tenantcluster",
            name="node_labels",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Arbitrary operator-defined labels for runs_on matching "
                    "(e.g. ['gpu', 'high-memory', 'spot']). "
                    "Case-insensitive in dispatch matching."
                ),
            ),
        ),
    ]
