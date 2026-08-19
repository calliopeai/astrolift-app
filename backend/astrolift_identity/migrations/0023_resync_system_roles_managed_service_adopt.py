"""Re-upsert system roles so existing installs pick up ``managed_service.adopt`` (#1365).

Same shape as 0018/0021/0022: the source of truth is
``astrolift_identity.system_roles.SYSTEM_ROLES``, but the persisted
``permissions`` column is a JSON list of slug strings that only refreshes
on upsert. ``managed_service.adopt`` reaches ``org_owner`` and ``org_admin``
through their full-enum comprehensions, which are evaluated when the roles
are written -- so without this migration every existing install would carry
a stale list, adoption would deny for everybody, and the one migration path
off a pre-identity-tag resource would be unreachable on exactly the installs
that have such resources.
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
        ("astrolift_identity", "0022_resync_system_roles_agent_box_attach"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
