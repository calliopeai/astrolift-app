"""
Re-upsert system roles so existing installs pick up newly-added
permissions in ``core.permissions.Permission``.

org_owner is defined as ``tuple(Permission)`` and org_admin as
``tuple(p for p in Permission if p not in (...))`` — they both
auto-include every permission added to the enum. Adding the SCM
slugs (``scm.read``, ``scm.connect``, …) without re-running this
upsert leaves existing role rows stuck on the old slug list, so
the SCM nav entry hides for everyone except superusers.

The body mirrors ``0002_system_roles.upsert_system_roles``; we
inline rather than import because Python module names can't start
with a digit, so cross-migration imports need ``importlib`` gymnastics
that aren't worth saving four lines.
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
        ("astrolift_identity", "0003_identityprovider_display_name"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
