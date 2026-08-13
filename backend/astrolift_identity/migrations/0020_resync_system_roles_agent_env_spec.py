"""Re-upsert system roles for dedicated agent environment-spec grants.

Environment-spec resolvers originally reused broad ``app.*`` permissions even
though the dedicated ``agent_env_spec.*`` catalog already existed. Re-gating
the resolvers without refreshing persisted system roles would remove existing
access from non-org roles, because role permission lists are stored as JSON.
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
                "permissions": [permission.value for permission in perms],
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0019_resync_system_roles_agent_workflow"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
