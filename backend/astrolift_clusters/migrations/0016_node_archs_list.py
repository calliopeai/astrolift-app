"""`node_arch` becomes `node_archs`, a list (#1604).

A cluster with amd64 and arm64 node groups is ordinary, and the CharField
could not say so. The dispatch matcher did `cluster.node_arch not in
required_arch`, which forces every cluster to claim exactly one architecture
or none -- so a producer had no honest value to write for a mixed fleet, and
the column sat at its empty default while every arch-labelled job routed
nowhere.

Add-copy-remove rather than the auto-generated remove-then-add. Nothing in
the repo ever wrote `node_arch`, so in practice every row is empty and the
copy is a no-op; it is here because the only way a value could exist is an
operator having set it by hand, and that is exactly the value worth not
discarding.
"""

from __future__ import annotations

from django.db import migrations, models


def _carry_forward(apps, schema_editor):
    TenantCluster = apps.get_model("astrolift_clusters", "TenantCluster")
    for pk, arch in TenantCluster.objects.exclude(node_arch="").values_list("pk", "node_arch"):
        value = (arch or "").strip()
        if value:
            TenantCluster.objects.filter(pk=pk).update(node_archs=[value])


def _carry_back(apps, schema_editor):
    """Reverse takes the first architecture, which is lossy for a genuinely
    mixed cluster. That is unavoidable -- the column it reverses into holds
    one value -- and is why the forward direction exists."""
    TenantCluster = apps.get_model("astrolift_clusters", "TenantCluster")
    for pk, archs in TenantCluster.objects.values_list("pk", "node_archs"):
        if archs:
            TenantCluster.objects.filter(pk=pk).update(node_arch=str(archs[0]))


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_clusters", "0015_tenantcluster_ingress_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantcluster",
            name="node_archs",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Architectures present across this cluster's nodes, e.g. "
                    '["amd64", "arm64"]. Discovered on capability reconcile; '
                    "empty means not yet probed."
                ),
            ),
        ),
        migrations.RunPython(_carry_forward, _carry_back),
        migrations.RemoveField(model_name="tenantcluster", name="node_arch"),
    ]
