from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0048_merge_azure_identity_native_removal"),
    ]

    # Reversing this must not drop the column. Migrations above a guarded
    # migration are reversed before the guard refuses, so a dropped column
    # would be erased by a rollback that is then refused. The column is kept
    # on reverse; the state change alone is reversed.
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="managedservice",
                    name="failed_step",
                    field=models.CharField(blank=True, default="", db_default="", max_length=32),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE astrolift_services_managedservice "
                        "ADD COLUMN IF NOT EXISTS failed_step varchar(32) NOT NULL DEFAULT ''"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
