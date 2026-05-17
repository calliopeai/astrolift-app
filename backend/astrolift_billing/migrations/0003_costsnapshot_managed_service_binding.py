"""Per-binding cost attribution (#432).

Adds a nullable FK from ``CostSnapshot`` to ``ManagedServiceBinding``
so the cost-collection driver can write per-resource rows.

The collector tags every cloud resource with
``astrolift.io/binding`` + ``astrolift.io/managed_service_id`` at
provision time; the daily cost activity reads the cloud provider's
billing API by tag and joins each row back to the binding row.
When the cloud-side cost driver can't attribute a cost to a single
binding (e.g. shared egress), the FK stays null and the snapshot
rolls up at the app level as before.

Also adds a uniqueness constraint so the daily collector can call
``update_or_create`` and stay idempotent across re-runs.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_billing", "0002_initial"),
        ("astrolift_services", "0002_alter_managedservice_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="costsnapshot",
            name="managed_service_binding",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="cost_snapshots",
                to="astrolift_services.managedservicebinding",
            ),
        ),
        migrations.AddIndex(
            model_name="costsnapshot",
            index=models.Index(
                fields=["organization", "managed_service_binding", "-taken_at"],
                name="cost_org_binding_date_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="costsnapshot",
            constraint=models.UniqueConstraint(
                fields=[
                    "organization",
                    "registered_app",
                    "managed_service_binding",
                    "by",
                    "taken_at",
                    "source",
                ],
                name="cost_snapshot_unique_per_day_scope",
            ),
        ),
    ]
