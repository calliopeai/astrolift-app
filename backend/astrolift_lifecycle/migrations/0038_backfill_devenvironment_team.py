# Builder dev environments now belong to a team (#1919): create stores it,
# sync/promote enforce it. ``team`` has been a nullable column since the
# very first migration (0023) but nothing ever set it, so every existing
# row has ``team_id IS NULL``. Derive it only where it's unambiguous: a
# dev environment already promoted to a RegisteredApp inherits that app's
# team (``RegisteredApp.team`` is required, never null -- a soft-deleted
# app still carries one, and the historical model here reads through the
# plain migration-state manager, not ``SoftDeleteManager``, so it counts
# too). A never-promoted row has nothing to derive and is left null, which
# the fix treats as org-admin-only.

from django.db import migrations


def backfill_team_from_promoted_app(apps, schema_editor):
    DevEnvironment = apps.get_model("astrolift_lifecycle", "DevEnvironment")
    RegisteredApp = apps.get_model("astrolift_registry", "RegisteredApp")

    candidates = list(
        DevEnvironment.objects.filter(team_id__isnull=True, promoted_app_id__isnull=False).only(
            "pk", "promoted_app_id"
        )
    )
    if not candidates:
        return

    team_by_app = dict(
        RegisteredApp.objects.filter(
            pk__in={row.promoted_app_id for row in candidates},
            team_id__isnull=False,
        ).values_list("pk", "team_id")
    )

    to_update = []
    for row in candidates:
        team_id = team_by_app.get(row.promoted_app_id)
        if team_id is not None:
            row.team_id = team_id
            to_update.append(row)
    if to_update:
        DevEnvironment.objects.bulk_update(to_update, ["team_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0037_devenvironment_data_file"),
        ("astrolift_registry", "0021_workload_kind_task_app_source_kind_direct_upload"),
    ]

    operations = [
        migrations.RunPython(backfill_team_from_promoted_app, migrations.RunPython.noop),
    ]
