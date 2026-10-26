"""Serialize PostgreSQL migrations from web startup, bootstrap, and operators."""

import time

from django.core.management.commands.migrate import Command as DjangoMigrateCommand
from django.db import connections

# PostgreSQL advisory locks are scoped to a database, across sessions and roles.
MIGRATION_LOCK_ID = int.from_bytes(b"AstroMig", "big")
LOCK_POLL_SECONDS = 0.5


class Command(DjangoMigrateCommand):
    def handle(self, *args, **options):
        connection = connections[options["database"]]
        if connection.vendor != "postgresql":
            return super().handle(*args, **options)

        # Lock before Django loads migration history, and hold through its
        # post-migrate hooks. Transaction locks cannot span non-atomic migrations.
        with connection.cursor() as cursor:
            self._acquire(cursor)
            try:
                return super().handle(*args, **options)
            finally:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [MIGRATION_LOCK_ID])

    def _acquire(self, cursor):
        # Poll instead of blocking in pg_advisory_lock (#2521). A blocked
        # statement holds a snapshot, and the holder's CREATE INDEX
        # CONCURRENTLY waits for every older snapshot, so the two deadlock.
        # Between autocommit polls this session holds no snapshot at all.
        waiting = False
        while True:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [MIGRATION_LOCK_ID])
            if cursor.fetchone()[0]:
                return
            if not waiting:
                waiting = True
                self.stdout.write("Waiting for another migrate to finish...")
            time.sleep(LOCK_POLL_SECONDS)
