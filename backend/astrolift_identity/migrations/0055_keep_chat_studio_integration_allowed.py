"""Keep the Chat Studio integration on where an organization uses it (#2551).

``CHAT_STUDIO_INTEGRATION_ALLOWED`` now defaults off, so a new install does not
offer the module until its install admin turns it on. On an install where a
live organization already has the module on, the switch is recorded as on, so
its builder API callers keep working. A switch already stored, either way, is
left alone.

Constance stores a switch's default the first time it is read, and every
console page reads this one, so an install that has served the console already
holds the old default, on. This matters for an upgrade that runs 0032 and this
migration together: 0032 turns the module on for organizations with a dev
environment before anything has read the switch, and the first read would
otherwise store the new default and refuse their builder API callers.

The reverse is a no-op: the earlier default was on.
"""

from __future__ import annotations

from django.db import migrations

KEY = "chat_studio_integration"
SWITCH = "CHAT_STUDIO_INTEGRATION_ALLOWED"


def keep_allowed_where_in_use(apps, schema_editor):
    from constance import settings as constance_settings
    from constance.codecs import dumps

    OrganizationModule = apps.get_model("astrolift_identity", "OrganizationModule")
    in_use = OrganizationModule.objects.filter(
        key=KEY,
        enabled=True,
        deleted_at__isnull=True,
        organization__deleted_at__isnull=True,
    ).exists()
    if not in_use:
        return
    Constance = apps.get_model("constance", "Constance")
    Constance.objects.get_or_create(
        key=f"{constance_settings.DATABASE_PREFIX}{SWITCH}",
        defaults={"value": dumps(True)},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0054_resync_system_roles_scm_write"),
        ("constance", "0003_drop_pickle"),
    ]

    operations = [
        migrations.RunPython(keep_allowed_where_in_use, migrations.RunPython.noop),
    ]
