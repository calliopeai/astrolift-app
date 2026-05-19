"""Event severity column + composite filter index (#540).

Adds an indexed ``severity`` column on ``Event`` so the mobile + web
activity surfaces can server-side filter by severity instead of
deriving it at render time. Backfill walks the existing event log
using the same heuristic the mobile client applied: explicit
``payload.severity`` / ``payload.level`` win, then ``payload.status``
(``failed|error`` → ``error``, ``warned|degraded`` → ``warn``), then
the ``event_type`` shape, then the ``info`` default.

The event table carries a row-level BEFORE UPDATE trigger that refuses
mutation (see ``0003_append_only_triggers``). The backfill therefore
``ALTER TABLE ... DISABLE TRIGGER`` for the duration of the
``UPDATE`` and re-enables on the way out — the schema migration itself
already wrote ``"info"`` to every row via the column default, so the
backfill only rewrites the non-``info`` minority.
"""

from __future__ import annotations

from django.db import migrations, models

# Heuristic mirrored from ``core.events.infer_event_severity``. Inlined
# in the migration so it stays stable even if the helper is renamed or
# the import path moves — migrations must be reproducible from a fresh
# checkout against any future codebase.
_CANONICAL = ("info", "warn", "error")


def _infer_severity(event_type: str, payload: dict | None) -> str:
    payload = payload or {}
    raw_sev = payload.get("severity")
    if isinstance(raw_sev, str) and raw_sev in _CANONICAL:
        return raw_sev
    raw_level = payload.get("level")
    if isinstance(raw_level, str) and raw_level in _CANONICAL:
        return raw_level
    raw_status = payload.get("status")
    if isinstance(raw_status, str):
        if raw_status in ("failed", "error"):
            return "error"
        if raw_status in ("warned", "degraded"):
            return "warn"
    et = (event_type or "").lower()
    if et.endswith(".failed") or "error" in et:
        return "error"
    if et.endswith(".warned") or "warn" in et:
        return "warn"
    return "info"


def backfill_severity(apps, schema_editor):
    Event = apps.get_model("astrolift_operations", "Event")
    connection = schema_editor.connection
    table = Event._meta.db_table

    # The schema migration above stamped ``severity = "info"`` on every
    # existing row via the column default. We only need to rewrite the
    # rows where the heuristic produces something else, and we have to
    # bypass the append-only trigger to do so. Disable the trigger for
    # the table within this transaction, run the update batch, then
    # re-enable. Postgres scopes ``ALTER TABLE ... DISABLE TRIGGER`` at
    # the session level, so wrapping the work between the two ALTERs is
    # sufficient.
    trigger_name = f"{table}_append_only"
    with connection.cursor() as cursor:
        cursor.execute(f'ALTER TABLE "{table}" DISABLE TRIGGER "{trigger_name}";')
        try:
            qs = Event.objects.all().only("pk", "event_type", "payload", "severity")
            # iterator() so we don't load the whole event log into memory
            # on installs that already have a populated event table.
            updates: list[tuple[str, int]] = []
            for row in qs.iterator(chunk_size=2000):
                computed = _infer_severity(row.event_type or "", row.payload or {})
                if computed != (row.severity or "info"):
                    updates.append((computed, row.pk))
                if len(updates) >= 2000:
                    cursor.executemany(
                        f'UPDATE "{table}" SET severity = %s WHERE id = %s;',
                        updates,
                    )
                    updates.clear()
            if updates:
                cursor.executemany(
                    f'UPDATE "{table}" SET severity = %s WHERE id = %s;',
                    updates,
                )
        finally:
            cursor.execute(f'ALTER TABLE "{table}" ENABLE TRIGGER "{trigger_name}";')


def noop_reverse(apps, schema_editor):
    """Forward backfill is non-destructive; the reverse direction drops
    the column entirely so per-row writes don't need to be undone."""


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0012_merge_0010_0011_leaves"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="severity",
            field=models.CharField(
                blank=True,
                choices=[("info", "info"), ("warn", "warn"), ("error", "error")],
                db_index=True,
                default="info",
                max_length=16,
            ),
        ),
        migrations.AddIndex(
            model_name="event",
            index=models.Index(
                fields=["organization", "-occurred_at", "severity"],
                name="event_org_time_sev_idx",
            ),
        ),
        migrations.RunPython(backfill_severity, reverse_code=noop_reverse),
    ]
