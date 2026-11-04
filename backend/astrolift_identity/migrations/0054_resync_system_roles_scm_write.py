"""Re-upsert system roles for ``scm.write`` (#2605).

Same shape as 0043. Org owner and admin hold every permission, so they gain
``scm.write`` (create a repository through a source connection). The persisted
``permissions`` list only refreshes when a role is written, so without this an
existing install's owners could not create one.

The reverse is a no-op, as in 0043.
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
        ("astrolift_identity", "0053_trusted_domain_signup"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
