"""Let the scheduled retention sweep delete, and nothing else (#1594).

`0003_append_only_triggers` binds `astrolift_refuse_mutation()` to Event and
AuditEvent so the storage layer refuses UPDATE and DELETE outright, per
spec/04 §1 modeling principle 7. The platform simultaneously promises
"retained for N days" in the UI and ships a daily prune schedule. Both were
shipped; only one could hold.

The decision recorded on #1594 is export-then-delete, so the guarantee is now
**append-only except scheduled retention**, and this is the narrowest form of
that I could find.

The trigger consults a session-local setting that only the retention sweep
sets. Deliberately not the alternatives:

* ``session_replication_role = replica`` disables *every* non-ALWAYS trigger
  in the session, including foreign-key enforcement, and needs elevated
  privileges. Far too wide for one DELETE.
* ``ALTER TABLE ... DISABLE TRIGGER`` takes an ACCESS EXCLUSIVE lock and is
  visible to *concurrent* sessions, so an unrelated write during the sweep
  would slip past the guard.
* A ``SECURITY DEFINER`` function does not help: privilege is not what stops
  the delete, the trigger is.

What this preserves, and what the tests hold:

* **UPDATE is still refused unconditionally**, sweep or no sweep. Retention
  is about how long a record is kept, never about editing one. An audit
  trail that can be rewritten is worth nothing, and the compliance argument
  for deletion does not extend to mutation.
* The gate is `SET LOCAL`, so it reverts at commit or rollback and cannot
  leak into another statement, another transaction, or a pooled connection
  handed to someone else.
* The refusal message names the sanctioned path, so anyone who hits it
  finds this rather than guessing.
"""

from __future__ import annotations

from django.db import migrations

GATED_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION astrolift_refuse_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- DELETE only, and only inside a transaction that has explicitly opted
    -- in. `current_setting(..., true)` returns NULL rather than raising when
    -- the setting was never set, which is every ordinary transaction.
    IF TG_OP = 'DELETE'
       AND coalesce(current_setting('astrolift.retention_sweep', true), '') = 'on'
    THEN
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'astrolift: % refused on append-only table %.% '
        '(retention deletes must run through the scheduled sweep, see #1594)',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = '25006';
    RETURN NULL;
END;
$$;
"""

UNGATED_TRIGGER_FN = """
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


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0022_auditarchive"),
    ]

    operations = [
        migrations.RunSQL(sql=GATED_TRIGGER_FN, reverse_sql=UNGATED_TRIGGER_FN),
    ]
