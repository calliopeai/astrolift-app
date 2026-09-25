"""Index ``MutationAuditLog.organization`` without locking the table (#1955).

``CREATE INDEX CONCURRENTLY`` lets inserts and reads continue while it
builds, and cannot run inside a transaction, hence ``atomic = False``. The
key is ``(organization, -timestamp)``: the lifecycle audit filters by org
and reads newest first, and the leading column serves the FK as well.

If the build fails partway, Postgres keeps an INVALID index under this
name and a rerun reports that it already exists: drop
``core_mal_org_time_idx`` and migrate again.
"""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("core", "0013_mutationauditlog_organization"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="mutationauditlog",
            index=models.Index(fields=["organization", "-timestamp"], name="core_mal_org_time_idx"),
        ),
    ]
