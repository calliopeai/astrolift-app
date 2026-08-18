"""Re-upsert system roles so existing installs pick up the new
``agent_box.attach`` permission added for #129.

Same shape as 0021 (which did this for ``agent_task.send_input``): the
source of truth is ``astrolift_identity.system_roles.SYSTEM_ROLES``, but
the persisted ``permissions`` column is a JSON list of slug strings that
only refreshes on upsert. Without this migration every existing role —
including ``org_owner``, which holds the whole enum by comprehension —
would carry a stale list missing ``agent_box.attach``, so the exec relay
would deny every box attach and #129 would look unfixed.
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
        ("astrolift_identity", "0021_resync_system_roles_agent_task_send_input"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
