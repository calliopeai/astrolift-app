"""Re-upsert system roles for ``cluster.read`` (#2295).

Same shape as 0036. Cluster inventory reads moved from ``cluster.register``
to ``cluster.read``, held by Cluster Owner and the viewer/auditor read set
(org owner and admin hold every permission). The persisted ``permissions``
list only refreshes when a role is written, so without this an existing
install's roles never gain it and their cluster list goes dark.

The reverse is a no-op, as in 0036.
"""

from __future__ import annotations

from django.db import migrations


def upsert_system_roles(apps, schema_editor):
    Role = apps.get_model("astrolift_identity", "Role")
    from astrolift_identity.system_roles import SYSTEM_ROLES

    for slug, scope_level, name, description, perms in SYSTEM_ROLES:
        Role.objects.update_or_create(
            slug=slug,
            is_system=True,
            organization=None,
            defaults={
                "name": name,
                "description": description,
                "scope_level": scope_level,
                "permissions": [p.value for p in perms],
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0042_team_membership_actions"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
