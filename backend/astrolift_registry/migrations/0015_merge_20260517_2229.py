# Merge migration for the two parallel 0014 merge migrations that
# landed via sibling agents (both consolidate the same 0013 split).
# No schema changes — just unifies the leaf so subsequent migrations
# have a single ancestor.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0014_merge_0013_apps_indexes_and_secret_approval"),
        ("astrolift_registry", "0014_merge_20260517_2129"),
    ]

    operations = []
