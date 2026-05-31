"""Merge migration — resolves the 0008 conflict between
manageddomain_provision_fields and tenantcluster_node_capability_fields,
both of which depend on 0007_clusterbootstraprun."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_clusters", "0008_manageddomain_provision_fields"),
        ("astrolift_clusters", "0008_tenantcluster_node_capability_fields"),
    ]

    operations = []
