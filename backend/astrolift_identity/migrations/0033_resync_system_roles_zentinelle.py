"""Re-upsert system roles so existing installs pick up the Zentinelle permissions (#1887).

Same shape as 0023: ``zentinelle.connect`` and ``zentinelle.gateway_manage``
reach ``org_owner`` and ``org_admin`` through their full-enum comprehensions,
which are evaluated when the roles are written. Without this migration the
persisted ``permissions`` list on every existing install stays stale and both
gates deny even an org owner.
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
        ("astrolift_identity", "0032_enable_chat_studio_module_for_builder_orgs"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
