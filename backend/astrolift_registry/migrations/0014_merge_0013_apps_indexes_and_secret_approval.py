# Merge migration for the two 0013 leaf nodes that landed in parallel
# (#481 apps-list indexes + #488 secret-change approval). Both
# migrations have ``("astrolift_registry", "0012_registeredapp_webhook_deploys_paused")``
# as their sole dependency and touch disjoint columns, so a no-op
# merge resolves the leaf split.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0013_registeredapp_apps_list_indexes"),
        ("astrolift_registry", "0013_registeredapp_secret_approval_policy"),
    ]

    operations = []
