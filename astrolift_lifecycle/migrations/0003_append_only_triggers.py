"""
Append-only enforcement for ``DeploymentLog``.

Re-uses the ``astrolift_refuse_mutation()`` function created by
``astrolift_operations/migrations/0003_append_only_triggers.py`` so
all three append-only tables share one trigger body.
"""

from __future__ import annotations

from django.db import migrations


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
        ("astrolift_lifecycle", "0002_initial"),
        # Forces the trigger function migration to run first so the
        # CREATE TRIGGER below has something to point at.
        ("astrolift_operations", "0003_append_only_triggers"),
    ]

    operations = [
        migrations.RunSQL(
            sql=_attach("astrolift_lifecycle_deploymentlog"),
            reverse_sql=_detach("astrolift_lifecycle_deploymentlog"),
        ),
    ]
