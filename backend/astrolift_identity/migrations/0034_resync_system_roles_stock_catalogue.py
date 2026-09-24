"""Re-upsert system roles so every install carries the stock role catalogue (#1864).

Same shape as 0033. The catalogue adds three roles (``org_viewer``,
``team_operator``, ``project_operator``) and the #1888 Zentinelle defaults on
the existing ones. A fresh install already gets all of it from 0002, which
reads the live catalogue, but the persisted ``permissions`` list only
refreshes when a role is written: without this migration an existing install
keeps its old lists and never gains the new roles.

The reverse is a no-op. Removing the new roles would be refused as soon as
anyone holds one (``RoleBinding.role`` is ``PROTECT``), and after a code
rollback the older resolver never matches the slugs it does not know.
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
        ("astrolift_identity", "0033_resync_system_roles_zentinelle"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
