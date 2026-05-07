"""
Idempotent upsert of the system role catalog.

Re-runnable on every deploy. ``is_system=True`` rows can be matched
by slug; we rewrite their permissions list to keep the DB in sync
with the source of truth in ``astrolift_identity.system_roles``.
"""

from __future__ import annotations

from django.db import migrations


def upsert_system_roles(apps, schema_editor):
    Role = apps.get_model("astrolift_identity", "Role")

    # Import here to avoid the model-graph being unavailable when
    # Django loads migration files at startup.
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


def remove_system_roles(apps, schema_editor):
    Role = apps.get_model("astrolift_identity", "Role")
    Role.objects.filter(is_system=True, organization__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(upsert_system_roles, remove_system_roles),
    ]
