"""Migration coordination uses real PostgreSQL sessions, including lock waits."""

from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Event
from time import monotonic

import pytest
from django.core.management import call_command, get_commands
from django.core.management.commands.migrate import Command as DjangoMigrateCommand
from django.db import DatabaseError, connection, connections, transaction
from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteConnection

from core.management.commands import migrate

pytestmark = pytest.mark.django_db(transaction=True)


def _locks(*, granted):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pid FROM pg_locks WHERE locktype = 'advisory' "
            "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "AND classid::bigint = %s AND objid::bigint = %s AND objsubid = 1 AND granted = %s",
            [migrate.MIGRATION_LOCK_ID >> 32, migrate.MIGRATION_LOCK_ID & 0xFFFFFFFF, granted],
        )
        return [row[0] for row in cursor.fetchall()]


def _assert_released():
    probe = connection.copy()
    try:
        with probe.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [migrate.MIGRATION_LOCK_ID])
            acquired = cursor.fetchone()[0]
            if acquired:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [migrate.MIGRATION_LOCK_ID])
        assert acquired
    finally:
        probe.close()


def test_manage_migrate_discovers_the_coordinated_command_and_keeps_plan_option():
    assert get_commands()["migrate"] == "core"
    output = StringIO()
    call_command("migrate", plan=True, database="default", stdout=output, verbosity=1)
    assert "Planned operations:" in output.getvalue()
    _assert_released()


def test_concurrent_commands_wait_before_django_loads_the_migration_plan(monkeypatch):
    first_entered = Event()
    release_first = Event()
    entered = []

    def migration_work(self, *args, **options):
        # Substitute only Django's migration work. The command and PostgreSQL
        # connection/lock acquisition execute normally in both worker threads.
        entered.append(options["database"])
        if len(entered) == 1:
            first_entered.set()
            assert release_first.wait(10), "first migration was not released"

    monkeypatch.setattr(DjangoMigrateCommand, "handle", migration_work)

    second_output = StringIO()

    def run(stdout=None):
        try:
            call_command("migrate", database="default", verbosity=0, skip_checks=True, stdout=stdout)
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run)
        try:
            assert first_entered.wait(5)
            second = executor.submit(run, second_output)
            deadline = monotonic() + 5
            while "Waiting for another migrate" not in second_output.getvalue():
                assert monotonic() < deadline, "second migration did not wait on PostgreSQL"
                Event().wait(0.01)
            # It polls; it is never queued on the lock (#2521).
            assert _locks(granted=False) == []
            assert entered == ["default"]
        finally:
            release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert entered == ["default", "default"]
    _assert_released()


def _lock_waiters(holder_pid):
    """Backends other than the holder that have asked for the migration lock."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
            "AND pid NOT IN (pg_backend_pid(), %s) AND query LIKE %s",
            [holder_pid, f"%advisory_lock({migrate.MIGRATION_LOCK_ID})%"],
        )
        return [row[0] for row in cursor.fetchall()]


def test_waiting_migrate_does_not_deadlock_a_concurrent_index_build(monkeypatch):
    """#2521: a migrate waiting for the lock must not hold a snapshot.

    The holder's CREATE INDEX CONCURRENTLY waits for every older snapshot. A
    waiter blocked in pg_advisory_lock holds one while it waits for the
    holder, and Postgres cancels one of the two as a deadlock.
    """
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE s2521_build (id integer)")
    holder = {}
    holder_locked = Event()
    second_waiting = Event()
    built = []

    def migration_work(self, *args, **options):
        if holder:
            return  # the second migrate, once the first is done: nothing to apply
        holder["pid"] = _pid(options["database"])
        holder_locked.set()
        with connections[options["database"]].cursor() as cursor:
            assert second_waiting.wait(10), "second migrate never asked for the lock"
            cursor.execute("CREATE INDEX CONCURRENTLY s2521_build_idx ON s2521_build (id)")
            built.append(True)

    monkeypatch.setattr(DjangoMigrateCommand, "handle", migration_work)

    def run():
        try:
            call_command("migrate", database="default", verbosity=0, skip_checks=True, stdout=StringIO())
        finally:
            connections["default"].close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(run)
            assert holder_locked.wait(10)
            second = executor.submit(run)
            deadline = monotonic() + 10
            while not _lock_waiters(holder["pid"]):
                assert monotonic() < deadline, "second migrate never asked for the lock"
                Event().wait(0.01)
            second_waiting.set()
            first.result(timeout=30)
            second.result(timeout=30)
        assert built == [True]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 's2521_build_idx'"
            )
            assert cursor.fetchone() == (True,)
        _assert_released()
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS s2521_build")


def _pid(alias):
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


@pytest.mark.parametrize("database_failure", [False, True])
def test_failed_migration_releases_the_lock_for_the_next_process(monkeypatch, database_failure):
    def fail(self, *args, **options):
        assert _locks(granted=True)
        if database_failure:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT 1 / 0")
        raise RuntimeError("migration failed")

    monkeypatch.setattr(DjangoMigrateCommand, "handle", fail)
    with pytest.raises(DatabaseError if database_failure else RuntimeError):
        call_command("migrate", verbosity=0, skip_checks=True)
    _assert_released()


def test_database_alias_selects_the_session_that_holds_the_lock(monkeypatch):
    alternate = connection.copy(alias="migration_target")
    connections[alternate.alias] = alternate
    try:
        with alternate.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pid = cursor.fetchone()[0]

        def migration_work(self, *args, **options):
            assert _locks(granted=True) == [pid]
            assert options == {"database": "migration_target", "fake": True}
            return "django result"

        monkeypatch.setattr(DjangoMigrateCommand, "handle", migration_work)
        assert migrate.Command().handle(database="migration_target", fake=True) == "django result"
        _assert_released()
    finally:
        alternate.close()
        del connections[alternate.alias]


def test_non_postgresql_retains_django_behavior(monkeypatch):
    sqlite = SQLiteConnection({"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"})
    monkeypatch.setattr(migrate, "connections", {"sqlite": sqlite})
    calls = []

    def migration_work(self, *args, **options):
        calls.append(options)
        return "django result"

    monkeypatch.setattr(DjangoMigrateCommand, "handle", migration_work)
    assert migrate.Command().handle(database="sqlite", fake_initial=True) == "django result"
    assert calls == [{"database": "sqlite", "fake_initial": True}]
