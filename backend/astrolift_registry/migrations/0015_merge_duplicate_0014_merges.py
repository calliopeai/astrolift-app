# Both 0014_* migrations are no-op merges of the same 0013 leaf
# pair (apps_list_indexes + secret_approval_policy), introduced by
# parallel branches that auto-generated and hand-wrote the same
# merge. Result: two leaf nodes, Django refuses to run any further
# migration on this app. This file collapses them back to a single
# leaf so subsequent migrations have a stable point to chain off.
#
# No-op operations on both ends — purely a graph fix.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0014_merge_0013_apps_indexes_and_secret_approval"),
        ("astrolift_registry", "0014_merge_20260517_2129"),
    ]

    operations = []
