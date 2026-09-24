"""Let existing per-environment secret metadata rows follow the app-wide scope (#1758).

``0026`` made ``AppSecretMetadata.scope`` nullable: a per-environment row
with no scope of its own follows its key's app-wide row. Every row written
before that stores a scope, and ``setAppSecretMetadata`` wrote ``all``
whenever a caller omitted one. Kept as stored, each such row is an explicit
pin, so an approved app-wide narrowing never reaches that environment.

This clears the scope of every live per-environment row whose scope equals
its key's scope in force at migration time: the app-wide row's scope, else
``all``. No environment's scope changes now, and those rows follow later
app-wide changes. A row whose scope differs is a real override and keeps it.

Batched by primary key, so a large table is never read in one go. Each
UPDATE re-checks the scope it compared, so a row changed in the meantime
is left alone. Running it again changes nothing more; the reverse is a no-op.
"""

from __future__ import annotations

from collections import defaultdict

from django.db import migrations

BATCH_SIZE = 1000


def _in_force(app_wide_scope: str | None) -> str:
    # astrolift_services.secret_metadata_ops.scope_in_force for a row with
    # no scope of its own, inlined so the migration does not change when the
    # app code does.
    return app_wide_scope if app_wide_scope is not None else "all"


def follow_matching_app_wide_scope(apps, schema_editor, batch_size: int = BATCH_SIZE) -> None:
    Metadata = apps.get_model("astrolift_services", "AppSecretMetadata")
    last_pk = 0
    while True:
        batch = list(
            Metadata.objects.filter(pk__gt=last_pk, deleted_at__isnull=True, scope__isnull=False)
            .exclude(environment_name="")
            .order_by("pk")
            .values_list("pk", "registered_app_id", "key", "scope")[:batch_size]
        )
        if not batch:
            return
        last_pk = batch[-1][0]
        app_wide = {
            (app_id, key): scope
            for app_id, key, scope in Metadata.objects.filter(
                environment_name="",
                deleted_at__isnull=True,
                registered_app_id__in={app_id for _, app_id, _, _ in batch},
                key__in={key for _, _, key, _ in batch},
            ).values_list("registered_app_id", "key", "scope")
        }
        follow: dict[str, list[int]] = defaultdict(list)
        for pk, app_id, key, scope in batch:
            if scope == _in_force(app_wide.get((app_id, key))):
                follow[scope].append(pk)
        for scope, pks in follow.items():
            Metadata.objects.filter(pk__in=pks, scope=scope).update(scope=None)


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0026_appsecretmetadata_scope_follows_app_wide"),
    ]

    operations = [
        migrations.RunPython(follow_matching_app_wide_scope, migrations.RunPython.noop),
    ]
