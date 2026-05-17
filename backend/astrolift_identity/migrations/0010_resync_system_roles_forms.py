"""
Re-upsert system roles so existing installs pick up the new
``form.*`` permissions added for #453.

Same shape as 0008: the source of truth is
``astrolift_identity.system_roles.SYSTEM_ROLES``. ``org_owner`` and
``org_admin`` auto-include every new permission via comprehensions
over the ``Permission`` enum, but the persisted ``permissions``
column is a JSON list of slug strings — it only refreshes on upsert.
Without this migration existing installs would have a stale list
missing ``form.read`` / ``form.create`` / ``form.update`` /
``form.delete`` / ``form.submit`` / ``form.moderate``.

We also rewrite the team-level roles so ``team_viewer`` /
``team_developer`` pick up ``form.read`` + ``form.submit`` (so an
ordinary member can submit a published form), and ``org_auditor``
picks up ``form.read``.
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
        ("astrolift_identity", "0009_apitoken_last_used_ip_agent"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
