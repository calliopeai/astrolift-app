"""Make ``RegisteredApp.project`` nullable with SET_NULL on delete.

Backs the ``assignAstroliftAppToProject`` mutation (#391). The FK was
``null=False, on_delete=CASCADE`` since the table was created — that
made it impossible to "unassign" an app from a project, and meant
hard-deleting a project would silently nuke every app under it (the
platform always soft-deletes business rows, but the hard-delete escape
hatch shouldn't take apps with it).

The data backfill is a no-op: every existing row already points at a
real project (the registerApp mutation has always required a
``project_id``), so loosening the constraint is purely additive.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0001_initial"),
        ("astrolift_registry", "0008_registeredapp_source_webhook_id_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="registeredapp",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="registered_apps",
                to="astrolift_identity.project",
            ),
        ),
    ]
