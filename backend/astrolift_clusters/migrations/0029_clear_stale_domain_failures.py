"""Clear the failure that a later provisioning run left behind (#2519).

``provision_failed_step`` and ``provision_failure_reason`` say where and why a
run ended without activating the zone, and are recorded with
``provision_state='failed'``. Until #2519 a later run never cleared them, so a
zone that a Revalidate run took on to ``mark_active`` (on CONFLICT,
``astro.conflict.cloud``) still reported the earlier run's step and reason. A
row in any other state has moved on since they were recorded.
"""

from django.db import migrations
from django.db.models import Q


def clear_stale_failures(apps, schema_editor):
    ManagedDomain = apps.get_model("astrolift_clusters", "ManagedDomain")
    ManagedDomain.objects.exclude(provision_state="failed").filter(
        ~Q(provision_failed_step="") | ~Q(provision_failure_reason="")
    ).update(provision_failed_step="", provision_failure_reason="")


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_clusters", "0028_merge_identity_rebind_product_deployments"),
    ]

    operations = [
        migrations.RunPython(clear_stale_failures, migrations.RunPython.noop),
    ]
