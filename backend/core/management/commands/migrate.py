"""Serialize PostgreSQL migrations from web startup, bootstrap, and operators."""

from django.core.management.commands.migrate import Command as DjangoMigrateCommand
from django.db import connections

# PostgreSQL advisory locks are scoped to a database, across sessions and roles.
MIGRATION_LOCK_ID = int.from_bytes(b"AstroMig", "big")


class Command(DjangoMigrateCommand):
    def handle(self, *args, **options):
        connection = connections[options["database"]]
        if connection.vendor != "postgresql":
            return super().handle(*args, **options)

        # Lock before Django loads migration history, and hold through its
        # post-migrate hooks. Transaction locks cannot span non-atomic migrations.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [MIGRATION_LOCK_ID])
            try:
                return super().handle(*args, **options)
            finally:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [MIGRATION_LOCK_ID])
