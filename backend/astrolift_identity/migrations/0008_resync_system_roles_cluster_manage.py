"""
Re-upsert system roles so existing installs pick up
``cluster.manage`` + the new ``cluster_owner`` role (#316).

Same shape as 0002 / 0004: the source of truth is
``astrolift_identity.system_roles.SYSTEM_ROLES``; this migration just
rewrites the persisted permissions list for every system role so the
catalog stays in sync.

Why we re-upsert instead of patching: org_owner is defined as
``tuple(Permission)`` and org_admin as ``tuple(p for p in Permission
if p not in (ORG_DELETE, BILLING_UPDATE))`` — both auto-include any
new permission added to the enum, but the persisted ``permissions``
column is a JSON list of slug strings and only refreshes on upsert.
Without this migration, existing installs would have a stale
permission list that's missing ``cluster.manage`` (and ``scm.*``,
which 0004 already handled — the pattern is identical).
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
        ("astrolift_identity", "0007_organizationallowlisteddomain"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, migrations.RunPython.noop),
    ]
