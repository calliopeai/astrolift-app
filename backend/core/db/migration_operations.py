"""Migration operations that a non-atomic migration can run again (#2521).

A migration with ``atomic = False`` commits each statement as it goes. If one
step fails, the earlier steps stay applied and the migration stays unrecorded,
so the next ``migrate`` runs every step again. These operations skip what is
already there and emit exactly the SQL of the Django operation they extend
when it is not (``sqlmigrate`` always shows that SQL), so a database that applied the migration in one go and one
that got there on a second run end up the same.
"""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations


def _column_exists(schema_editor, table, column):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
            [table, column],
        )
        return cursor.fetchone() is not None


def _index_validity(schema_editor, name):
    """``True`` for a valid index, ``False`` for an invalid one, ``None`` for none."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = %s "
            "AND c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = current_schema())",
            [name],
        )
        row = cursor.fetchone()
        return None if row is None else row[0]


class AddFieldIfMissing(migrations.AddField):
    """``AddField`` that leaves an existing column alone.

    Only for a column Django adds in one statement, so a column that exists
    is the complete column: no deferred index or constraint.
    """

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        column = model._meta.get_field(self.name).column
        if not schema_editor.collect_sql and _column_exists(schema_editor, model._meta.db_table, column):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)


class AddIndexConcurrentlyIfMissing(AddIndexConcurrently):
    """``AddIndexConcurrently`` that rebuilds an interrupted build.

    A cancelled ``CREATE INDEX CONCURRENTLY`` leaves an INVALID index under
    its name, which ``IF NOT EXISTS`` would skip and leave invalid. Drop it
    concurrently and build again; leave a valid one alone. The migrate lock
    keeps any other migrator from building it at the same time.
    """

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        self._ensure_not_in_transaction(schema_editor)
        if schema_editor.collect_sql:  # sqlmigrate shows the build itself
            return super().database_forwards(app_label, schema_editor, from_state, to_state)
        validity = _index_validity(schema_editor, self.index.name)
        if validity is True:
            return
        if validity is False:
            schema_editor.execute(
                f"DROP INDEX CONCURRENTLY IF EXISTS {schema_editor.quote_name(self.index.name)}"
            )
        super().database_forwards(app_label, schema_editor, from_state, to_state)
