"""Org-level default resource tags (#1505).

`ProvisionSpec.tags` is read by twenty-one drivers and was set by nobody,
so no operator-defined tag has ever reached a cloud resource and
`cost_center` had no path to one at all. This is where they come from.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0023_resync_system_roles_managed_service_adopt"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="default_resource_tags",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
