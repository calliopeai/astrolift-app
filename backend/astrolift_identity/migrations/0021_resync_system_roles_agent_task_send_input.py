"""Re-upsert system roles so existing installs pick up the new
``agent_task.send_input`` permission added for #1390.

Same shape as 0018 (which did this for ``agent_task.watch``): the source
of truth is ``astrolift_identity.system_roles.SYSTEM_ROLES``. ``org_owner``
and ``org_admin`` auto-include every new permission via comprehensions
over the ``Permission`` enum, but the persisted ``permissions`` column is
a JSON list of slug strings — it only refreshes on upsert. Without this
migration existing installs would carry a stale list missing
``agent_task.send_input``, so ``sendAgentTaskInput`` would deny even an
org owner and the steering channel would look broken rather than
ungranted.
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
        ("astrolift_identity", "0020_resync_system_roles_agent_env_spec"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
