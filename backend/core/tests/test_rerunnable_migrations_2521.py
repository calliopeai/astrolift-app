"""Every non-atomic migration can run again after a partial failure (#2521).

Each test puts the database in a state a failed run leaves behind, with the
migration unrecorded, applies the migration the way ``migrate`` does, and
expects it recorded and the schema identical to one that applied it in one go.
"""

from pathlib import Path

import pytest
from django.db import OperationalError, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

pytestmark = pytest.mark.django_db(transaction=True)

CORE_0014 = ("core", "0014_mutationauditlog_org_time_index")
LIFECYCLE_0045 = ("astrolift_lifecycle", "0045_deployment_run_history")
LOG_TABLE = "astrolift_lifecycle_deploymentlog"
AUDIT_TABLE = "core_mutationauditlog"
CURSOR_IDX = "deploylog_cursor_idx"
AUDIT_IDX = "core_mal_org_time_idx"

BACKEND = Path(__file__).resolve().parents[2]
# Every migration with ``atomic = False`` in this repository's apps, not in
# installed packages. A new one must be added here, with a test that it can
# run again.
NON_ATOMIC = {CORE_0014, LIFECYCLE_0045}


def _execute(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall() if cursor.description else None


def _schema(table):
    """Columns and indexes of ``table``, as a fresh migrate creates them."""
    columns = _execute(
        "SELECT column_name, data_type, character_maximum_length, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s "
        "ORDER BY column_name",
        [table],
    )
    indexes = _execute(
        "SELECT c.relname, pg_get_indexdef(i.indexrelid), i.indisvalid, i.indisready "
        "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE i.indrelid = %s::regclass ORDER BY c.relname",
        [table],
    )
    return columns, indexes


def _index(name):
    rows = _execute(
        "SELECT i.indexrelid, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = %s",
        [name],
    )
    return rows[0] if rows else None


def _cancelled_concurrent_build(name, table, columns):
    """Leave the INVALID index a cancelled ``CREATE INDEX CONCURRENTLY`` leaves.

    An open snapshot on another session makes the build wait in its last
    phase, as the issue's deadlock did, and a statement timeout cancels it.
    """
    blocker = connection.copy()
    try:
        with blocker.cursor() as cursor:
            cursor.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("SELECT 1")
        _execute("SET statement_timeout = '500ms'")
        with pytest.raises(OperationalError, match="statement timeout"):
            _execute(f'CREATE INDEX CONCURRENTLY "{name}" ON "{table}" ({columns})')
    finally:
        _execute("SET statement_timeout = 0")
        with blocker.cursor() as cursor:
            cursor.execute("ROLLBACK")
        blocker.close()
    assert _index(name)[1] is False


def _rerun(key):
    """Apply one migration as ``migrate`` would, after a failed run left it unrecorded."""
    MigrationRecorder(connection).migration_qs.filter(app=key[0], name=key[1]).delete()
    executor = MigrationExecutor(connection)
    migration = executor.loader.get_migration(*key)
    assert migration.atomic is False
    state = executor.loader.project_state(key, at_end=False)
    executor.apply_migration(state, migration)
    assert MigrationRecorder(connection).migration_qs.filter(app=key[0], name=key[1]).exists()


def _restore():
    """Put back what a test took apart, so one failure does not fail the rest."""
    for column, length in (("event", 32), ("phase", 16)):
        _execute(
            f'ALTER TABLE "{LOG_TABLE}" ADD COLUMN IF NOT EXISTS "{column}" varchar({length}) DEFAULT \'\' NOT NULL'
        )
    for name, table, columns in (
        (CURSOR_IDX, LOG_TABLE, '"deployment_id", "id"'),
        (AUDIT_IDX, AUDIT_TABLE, '"organization_id", "timestamp" DESC'),
    ):
        if (_index(name) or (None, False))[1] is not True:
            _execute(f'DROP INDEX IF EXISTS "{name}"')
            _execute(f'CREATE INDEX "{name}" ON "{table}" ({columns})')
    recorder = MigrationRecorder(connection)
    for key in NON_ATOMIC:
        if not recorder.migration_qs.filter(app=key[0], name=key[1]).exists():
            recorder.record_applied(*key)


@pytest.fixture
def schema():
    """Each table's schema before the test; restored after it whatever happens."""
    before = {LOG_TABLE: _schema(LOG_TABLE), AUDIT_TABLE: _schema(AUDIT_TABLE)}
    yield before
    _restore()


def test_every_non_atomic_migration_is_covered_here():
    executor = MigrationExecutor(connection)
    found = {
        key
        for key, migration in executor.loader.disk_migrations.items()
        if not migration.atomic and (BACKEND / migration.__module__.split(".")[0]).is_dir()
    }
    assert found == NON_ATOMIC


def test_lifecycle_0045_reruns_with_its_columns_added_and_its_index_invalid(schema):
    # conflict-two (#2521): the AddFields committed, the index build was
    # cancelled, the migration was never recorded.
    _execute(f'DROP INDEX "{CURSOR_IDX}"')
    _cancelled_concurrent_build(CURSOR_IDX, LOG_TABLE, '"deployment_id", "id"')

    _rerun(LIFECYCLE_0045)

    assert _index(CURSOR_IDX)[1] is True
    assert _schema(LOG_TABLE) == schema[LOG_TABLE]


def test_lifecycle_0045_reruns_with_only_one_column_added(schema):
    _execute(f'DROP INDEX "{CURSOR_IDX}"')
    _execute(f'ALTER TABLE "{LOG_TABLE}" DROP COLUMN "phase"')

    _rerun(LIFECYCLE_0045)

    assert _index(CURSOR_IDX)[1] is True
    assert _schema(LOG_TABLE) == schema[LOG_TABLE]


def test_lifecycle_0045_from_nothing_matches_a_fresh_migrate(schema):
    _execute(f'DROP INDEX "{CURSOR_IDX}"')
    _execute(f'ALTER TABLE "{LOG_TABLE}" DROP COLUMN "event", DROP COLUMN "phase"')

    _rerun(LIFECYCLE_0045)

    assert _schema(LOG_TABLE) == schema[LOG_TABLE]


def test_core_0014_reruns_over_the_invalid_index_a_cancelled_build_left(schema):
    _execute(f'DROP INDEX "{AUDIT_IDX}"')
    _cancelled_concurrent_build(AUDIT_IDX, AUDIT_TABLE, '"organization_id", "timestamp" DESC')

    _rerun(CORE_0014)

    assert _index(AUDIT_IDX)[1] is True
    assert _schema(AUDIT_TABLE) == schema[AUDIT_TABLE]


@pytest.mark.parametrize(
    ("key", "index", "table"), [(LIFECYCLE_0045, CURSOR_IDX, LOG_TABLE), (CORE_0014, AUDIT_IDX, AUDIT_TABLE)]
)
def test_a_complete_run_reruns_without_touching_anything(schema, key, index, table):
    oid = _index(index)[0]

    _rerun(key)

    assert _index(index)[0] == oid
    assert _schema(table) == schema[table]
