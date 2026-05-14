"""
Add cluster lifecycle state machine to TenantCluster (#316).

``lifecycle`` defaults to ``registered``. Existing active rows pre-#316
get the default — operators hit Refresh in /clusters once after deploy
to bring them into management properly. Acceptable for v1 because the
gate that surfaces deploy eligibility (wizard Step 1 filter +
registerApp PRECONDITION) will simply refuse until that one-time
refresh completes.

``last_management_error`` is empty until the workflow records a failure.
``managed_at`` is null until the first successful management run.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_clusters", "0003_tenantcluster_delivery_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantcluster",
            name="lifecycle",
            field=models.CharField(
                choices=[
                    ("registered", "Registered"),
                    ("managing", "Managing"),
                    ("managed", "Managed"),
                    ("error", "Error"),
                ],
                default="registered",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="tenantcluster",
            name="last_management_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="tenantcluster",
            name="managed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
