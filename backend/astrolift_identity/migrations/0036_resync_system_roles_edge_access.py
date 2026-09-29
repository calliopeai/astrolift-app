"""Re-upsert system roles for the edge-auth permissions (#2131, #2132).

Same shape as 0034. ``cluster.users`` (managing the edge identity provider's
users) goes to Cluster Owner, and ``app.access`` (who may enter an app behind
central auth) to Team Owner, Project Admin and App Admin. The persisted
``permissions`` list only refreshes when a role is written, so without this an
existing install's roles never gain them.

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
        ("astrolift_identity", "0035_alter_organizationmodule_key"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
