"""
Append-only enforcement at the database layer.

Spec/04 §1 modeling principle 7 calls for append-only logs to refuse
``UPDATE`` and ``DELETE`` at the storage layer, not just the ORM. This
migration installs a single shared trigger function and binds it to
``Event`` and ``AuditEvent``. The matching binding for
``DeploymentLog`` ships in ``astrolift_lifecycle/migrations/0003_*``.

The function lives in the ``public`` schema (the default) and raises
a SQLSTATE 25006 (read_only_sql_transaction) so the
``django.db.utils.OperationalError`` callers see is helpful rather
than opaque.
"""

from __future__ import annotations

from django.db import migrations


CREATE_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION astrolift_refuse_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'astrolift: % refused on append-only table %.%',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = '25006';
    RETURN NULL;
END;
$$;
"""

DROP_TRIGGER_FN = """
DROP FUNCTION IF EXISTS astrolift_refuse_mutation();
"""


def _attach(table: str) -> str:
    return f"""
DROP TRIGGER IF EXISTS {table}_append_only ON {table};
CREATE TRIGGER {table}_append_only
BEFORE UPDATE OR DELETE ON {table}
FOR EACH ROW EXECUTE FUNCTION astrolift_refuse_mutation();
"""


def _detach(table: str) -> str:
    return f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};"


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0002_event_provenance_fields"),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREATE_TRIGGER_FN,
            reverse_sql=DROP_TRIGGER_FN,
        ),
        migrations.RunSQL(
            sql=_attach("astrolift_operations_event"),
            reverse_sql=_detach("astrolift_operations_event"),
        ),
        migrations.RunSQL(
            sql=_attach("astrolift_operations_auditevent"),
            reverse_sql=_detach("astrolift_operations_auditevent"),
        ),
    ]
