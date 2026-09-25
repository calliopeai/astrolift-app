"""Record which org a cluster bootstrap run belongs to (#1955).

A shared cluster (organization NULL) resolves for every org, so
``bootstrapRuns`` / ``lastBootstrapRun`` returned every org's runs on it,
with the triggering username, the operator's host info and the error text.
The readers now filter on this column.

Backfill: a legacy run on an org-owned cluster takes the cluster's org.
Since #1184 only that org can resolve the cluster to record against it, and
the owner is who has always been shown every run on its cluster, so this
exposes nothing new. A legacy run on a shared cluster has no recorded
owner and stays NULL, which no tenant view shows.
"""

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def backfill_organization(apps, schema_editor):
    ClusterBootstrapRun = apps.get_model("astrolift_clusters", "ClusterBootstrapRun")
    TenantCluster = apps.get_model("astrolift_clusters", "TenantCluster")
    ClusterBootstrapRun.objects.filter(organization__isnull=True).update(
        organization_id=Subquery(
            TenantCluster.objects.filter(pk=OuterRef("tenant_cluster_id")).values("organization_id")[:1]
        )
    )


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_clusters', '0016_node_archs_list'),
        ('astrolift_identity', '0033_resync_system_roles_zentinelle'),
    ]

    operations = [
        migrations.AddField(
            model_name='clusterbootstraprun',
            name='organization',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='astrolift_identity.organization'),
        ),
        migrations.RunPython(backfill_organization, migrations.RunPython.noop),
    ]
