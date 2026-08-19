"""Merge the two 0020 leaves in astrolift_services (#1496).

#1488 (WorkloadIdentityGrant) and #1489 (ManagedResourceAdoption) both
branched from 0019 and landed back to back, leaving two leaf nodes. Django
refuses to build the migration graph in that state, so every Django test shard
on main errors during database setup. No operations: the two branches touch
different models and neither depends on the other.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0020_managedresourceadoption"),
        ("astrolift_services", "0020_workloadidentitygrant"),
    ]

    operations: list[migrations.operations.base.Operation] = []
