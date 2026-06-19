"""Re-upsert system roles so existing installs pick up the new Agents
and Workflows module permissions added for the entity-module re-shell
(spec 34/36 Phase 0).

Same shape as 0018: the source of truth is
``astrolift_identity.system_roles.SYSTEM_ROLES``. ``org_owner`` and
``org_admin`` auto-include every new permission via comprehensions over
the ``Permission`` enum, and the per-role tuples now seed the
``agent.*`` / ``workflow.*`` verbs onto the rest of the catalog
(admins → full, developers → view+create+run, viewers/auditor → read).
But the persisted ``permissions`` column is a JSON list of slug strings —
it only refreshes on upsert. Without this migration existing installs
would have a stale list missing the new module perms, so the
``me.modules`` capability manifest would under-report what an existing
role can actually do and the re-gated agent resolvers would reject
callers who previously had access via ``app.*``.
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
        ("astrolift_identity", "0018_resync_system_roles_agent_task_watch"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
